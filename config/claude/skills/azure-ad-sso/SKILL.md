---
name: azure-ad-sso
description: Implement Azure AD (Entra ID) SSO in a Next.js app deployed on Vercel. Zero dependencies — no MSAL, no NextAuth. Uses OAuth 2.0 authorization code flow with AES-256-GCM encrypted session cookies, Edge middleware, and Node.js route handlers. Use when the user asks to add Azure AD login, protect a Next.js app with Entra ID, or implement SSO without third-party auth libraries.
---

# Azure AD SSO for Next.js on Vercel

Implement zero-dependency Azure AD (Entra ID) SSO using OAuth 2.0 authorization code flow.
No NextAuth, no MSAL — just route handlers, middleware, and the Web Crypto / Node.js crypto APIs.

## Prerequisites

Check these before writing any code:

1. Confirm the project is **Next.js App Router** (has `src/app/` or `app/` directory).
2. Check `next.config.ts` — if `output: "export"` is set, **it must be removed**. Static export is incompatible with server-side route handlers.
3. Ask the user: **"Would you like me to produce the information your engineering team will need to set up the Azure AD App Registration?"** If yes, include the Azure AD setup section below in your response. If no, skip straight to Step 3.

## Architecture

```
Browser → Middleware (validates session cookie on every request)
  ├─ Valid session   → pass through
  └─ No/expired      → redirect to /api/auth/login?return_to=<original-path>
       → Azure AD authorize endpoint
       → Azure AD callback with ?code=
       → /api/auth/callback  (server-to-server token exchange)
       → Set encrypted session cookie
       → Redirect back to original page
```

Session state lives in an AES-256-GCM encrypted cookie. No database, no server-side session store.

## Azure AD App Registration (for the engineering team)

Provide this section to the user if they asked for it. It is intended to be handed to the engineering or infrastructure team who manage Azure AD.

The engineering team needs to:

**1. Create the app registration**

```bash
APP_NAME="my-nextjs-app"
VERCEL_URL="https://your-app.vercel.app"

az ad app create \
  --display-name "$APP_NAME" \
  --web-redirect-uris "$VERCEL_URL/api/auth/callback" \
  --sign-in-audience "AzureADMyOrg"
```

Note the `appId` from the output — this is `AZURE_AD_CLIENT_ID`.

**2. Create a client secret**

```bash
CLIENT_ID="<appId from previous step>"

az ad app credential reset \
  --id "$CLIENT_ID" \
  --display-name "vercel-prod" \
  --years 2
```

Note the `password` from the output — this is `AZURE_AD_CLIENT_SECRET`. It is shown only once.

**3. Get the tenant ID**

```bash
az account show --query tenantId -o tsv
```

This is `AZURE_AD_TENANT_ID`.

**4. Token claims**

Ensure the app's ID token includes `email`, `name`, and `preferred_username` claims. For most tenants this is the default when requesting the `openid profile email` scopes.

**What to hand back to the developer:**

| Value | Where it came from |
|---|---|
| `AZURE_AD_CLIENT_ID` | `appId` from app registration |
| `AZURE_AD_CLIENT_SECRET` | `password` from credential reset |
| `AZURE_AD_TENANT_ID` | Output of `az account show` |

## Step 1 — Vercel Environment Variables

Tell the user to set these in **Vercel → Project → Settings → Environment Variables**:

| Variable | Value |
|---|---|
| `AZURE_AD_CLIENT_ID` | Provided by the engineering team |
| `AZURE_AD_CLIENT_SECRET` | Provided by the engineering team |
| `AZURE_AD_TENANT_ID` | Provided by the engineering team |
| `SESSION_SECRET` | Run `openssl rand -hex 32` to generate |
| `PUBLIC_APP_URL` | Deployment URL, no trailing slash (e.g. `https://your-app.vercel.app`) |

## Step 2 — Update `next.config.ts`

Remove `output: "export"` if present. Route handlers require a server runtime.

```ts
import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  // Do NOT set output: "export" — SSO route handlers need a server runtime
};

export default nextConfig;
```

## Step 3 — Write the files

Write all five files below. Adapt import paths to match the project's actual directory structure.

### `src/lib/auth.ts`

```ts
import { createCipheriv, createDecipheriv, pbkdf2Sync, randomBytes } from 'node:crypto';

export interface SessionData {
  email: string;
  name: string;
  expiresAt: number; // unix seconds
}

export interface StateData {
  returnTo: string;
}

export interface TokenResponse {
  access_token: string;
  id_token: string;
  token_type: string;
  expires_in: number;
}

export const getConfig = () => ({
  clientId: process.env.AZURE_AD_CLIENT_ID ?? '',
  clientSecret: process.env.AZURE_AD_CLIENT_SECRET ?? '',
  tenantId: process.env.AZURE_AD_TENANT_ID ?? '',
  sessionSecret: process.env.SESSION_SECRET ?? '',
  appUrl: process.env.PUBLIC_APP_URL ?? '',
});

// AES-256-GCM — Node.js runtime only (used by route handlers)
// Format: base64url(iv).base64url(ciphertext).base64url(authTag)

function deriveKey(secret: string): Buffer {
  return pbkdf2Sync(secret, 'sso-session-salt', 100000, 32, 'sha256');
}

function encrypt(data: string, secret: string): string {
  const key = deriveKey(secret);
  const iv = randomBytes(12);
  const cipher = createCipheriv('aes-256-gcm', key, iv);
  const encrypted = Buffer.concat([cipher.update(data, 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return `${iv.toString('base64url')}.${encrypted.toString('base64url')}.${tag.toString('base64url')}`;
}

function decrypt(token: string, secret: string): string | null {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return null;
    const [ivStr, encStr, tagStr] = parts;
    const key = deriveKey(secret);
    const iv = Buffer.from(ivStr, 'base64url');
    const encrypted = Buffer.from(encStr, 'base64url');
    const tag = Buffer.from(tagStr, 'base64url');
    const decipher = createDecipheriv('aes-256-gcm', key, iv);
    decipher.setAuthTag(tag);
    const decrypted = Buffer.concat([decipher.update(encrypted), decipher.final()]);
    return decrypted.toString('utf8');
  } catch {
    return null;
  }
}

export function encryptSession(session: SessionData): string {
  const { sessionSecret } = getConfig();
  return encrypt(JSON.stringify(session), sessionSecret);
}

export function decryptSession(cookie: string): SessionData | null {
  const { sessionSecret } = getConfig();
  const json = decrypt(cookie, sessionSecret);
  if (!json) return null;
  try {
    return JSON.parse(json) as SessionData;
  } catch {
    return null;
  }
}

export function encryptState(state: StateData): string {
  const { sessionSecret } = getConfig();
  return encrypt(JSON.stringify(state), sessionSecret);
}

export function decryptState(token: string): StateData | null {
  const { sessionSecret } = getConfig();
  const json = decrypt(token, sessionSecret);
  if (!json) return null;
  try {
    return JSON.parse(json) as StateData;
  } catch {
    return null;
  }
}

// Safe without signature verification: id_token arrives via server-to-server
// POST to the Azure token endpoint (back-channel), not from the browser.
export function parseIdToken(
  idToken: string,
): { email: string; name: string; exp: number } | null {
  try {
    const parts = idToken.split('.');
    if (parts.length < 3) return null;
    const payload = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8'));
    if (typeof payload.exp !== 'number') return null;
    return {
      email: payload.email || payload.preferred_username || '',
      name: payload.name || '',
      exp: payload.exp,
    };
  } catch {
    return null;
  }
}

export function getAuthorizeUrl(state: string): string {
  const { clientId, tenantId, appUrl } = getConfig();
  const params = new URLSearchParams({
    client_id: clientId,
    response_type: 'code',
    redirect_uri: `${appUrl}/api/auth/callback`,
    scope: 'openid profile email',
    state,
  });
  return `https://login.microsoftonline.com/${tenantId}/oauth2/v2.0/authorize?${params.toString()}`;
}

export async function exchangeCodeForTokens(code: string): Promise<TokenResponse> {
  const { clientId, clientSecret, tenantId, appUrl } = getConfig();
  const response = await fetch(
    `https://login.microsoftonline.com/${tenantId}/oauth2/v2.0/token`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'authorization_code',
        code,
        redirect_uri: `${appUrl}/api/auth/callback`,
        client_id: clientId,
        client_secret: clientSecret,
      }).toString(),
    },
  );
  if (!response.ok) throw new Error('Token exchange failed');
  return response.json() as Promise<TokenResponse>;
}
```

**Important:** the `deriveKey` salt (`'sso-session-salt'` above) must be the same string in both `auth.ts` and `middleware.ts`. Choose any fixed string and use it consistently — changing it invalidates all existing sessions.

### `src/app/api/auth/login/route.ts`

```ts
import { NextResponse } from 'next/server';
import { encryptState, getAuthorizeUrl } from '../../../../lib/auth';

function sanitizeReturnTo(value: string | null): string {
  if (!value || !value.startsWith('/') || value.startsWith('//')) return '/';
  return value;
}

export async function GET(request: Request): Promise<NextResponse> {
  const url = new URL(request.url);
  const returnTo = sanitizeReturnTo(url.searchParams.get('return_to'));
  const state = encryptState({ returnTo });
  return NextResponse.redirect(getAuthorizeUrl(state));
}
```

### `src/app/api/auth/callback/route.ts`

```ts
import { NextResponse } from 'next/server';
import { decryptState, exchangeCodeForTokens, parseIdToken, encryptSession } from '../../../../lib/auth';

export async function GET(request: Request): Promise<NextResponse> {
  const url = new URL(request.url);
  const code = url.searchParams.get('code');
  const stateParam = url.searchParams.get('state');

  if (!code) return new NextResponse('Missing authorization code', { status: 400 });
  if (!stateParam) return new NextResponse('Missing state parameter', { status: 400 });

  const state = decryptState(stateParam);
  if (!state) return new NextResponse('Invalid state parameter', { status: 400 });

  let tokens;
  try {
    tokens = await exchangeCodeForTokens(code);
  } catch (err) {
    console.error('[auth] token exchange failed:', err);
    return new NextResponse('Token exchange failed', { status: 500 });
  }

  const userInfo = parseIdToken(tokens.id_token);
  if (!userInfo) return new NextResponse('Invalid token response', { status: 500 });

  const { email, name, exp } = userInfo;
  const ttl = exp - Math.floor(Date.now() / 1000);
  if (ttl <= 0) return new NextResponse('Token has already expired', { status: 400 });

  const MAX_SESSION_SECONDS = 8 * 60 * 60;
  const cappedExp = Math.min(exp, Math.floor(Date.now() / 1000) + MAX_SESSION_SECONDS);
  const session = encryptSession({ email, name, expiresAt: cappedExp });

  const response = NextResponse.redirect(new URL(state.returnTo, request.url));
  response.cookies.set('session', session, {
    httpOnly: true,
    secure: true,
    sameSite: 'lax',
    path: '/',
    maxAge: Math.min(ttl, MAX_SESSION_SECONDS),
  });
  return response;
}
```

### `src/app/api/auth/logout/route.ts`

```ts
import { NextResponse } from 'next/server';
import { getConfig } from '../../../../lib/auth';

export async function POST(): Promise<NextResponse> {
  const { tenantId, appUrl } = getConfig();
  const logoutUrl = `https://login.microsoftonline.com/${tenantId}/oauth2/v2.0/logout?post_logout_redirect_uri=${encodeURIComponent(appUrl)}`;
  const response = NextResponse.redirect(logoutUrl);
  response.cookies.set('session', '', {
    httpOnly: true, secure: true, sameSite: 'lax', path: '/', maxAge: 0,
  });
  return response;
}
```

### `src/middleware.ts`

Runs on the **Edge Runtime** — must use Web Crypto API, not `node:crypto`.
Use the same salt string you chose for `auth.ts`.

```ts
import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

async function deriveKey(secret: string): Promise<CryptoKey> {
  const encoder = new TextEncoder();
  const keyMaterial = await crypto.subtle.importKey(
    'raw', encoder.encode(secret), 'PBKDF2', false, ['deriveKey'],
  );
  return crypto.subtle.deriveKey(
    { name: 'PBKDF2', salt: encoder.encode('sso-session-salt'), iterations: 100000, hash: 'SHA-256' },
    keyMaterial,
    { name: 'AES-GCM', length: 256 },
    false,
    ['decrypt'],
  );
}

function base64urlToBuffer(str: string): Uint8Array {
  const base64 = str.replace(/-/g, '+').replace(/_/g, '/');
  const padding = '='.repeat((4 - (base64.length % 4)) % 4);
  const binary = atob(base64 + padding);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

async function validateSession(cookie: string, secret: string): Promise<boolean> {
  try {
    const parts = cookie.split('.');
    if (parts.length !== 3) return false;
    const [ivStr, encStr, tagStr] = parts;
    const iv = base64urlToBuffer(ivStr);
    const encrypted = base64urlToBuffer(encStr);
    const tag = base64urlToBuffer(tagStr);

    // Web Crypto AES-GCM expects ciphertext + authTag concatenated
    const ciphertext = new Uint8Array(encrypted.length + tag.length);
    ciphertext.set(encrypted);
    ciphertext.set(tag, encrypted.length);

    const key = await deriveKey(secret);
    const decrypted = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: iv.buffer as ArrayBuffer },
      key,
      ciphertext.buffer as ArrayBuffer,
    );

    const session = JSON.parse(new TextDecoder().decode(decrypted));
    return typeof session.expiresAt === 'number' && session.expiresAt > Date.now() / 1000;
  } catch {
    return false;
  }
}

export async function middleware(request: NextRequest): Promise<NextResponse> {
  // Bypass auth in development — no Azure credentials needed locally
  if (process.env.NODE_ENV === 'development') return NextResponse.next();

  const sessionCookie = request.cookies.get('session');
  const secret = process.env.SESSION_SECRET ?? '';

  if (sessionCookie && secret) {
    const valid = await validateSession(sessionCookie.value, secret);
    if (valid) return NextResponse.next();
  }

  const { pathname } = request.nextUrl;
  const returnTo = encodeURIComponent(pathname + request.nextUrl.search);
  return NextResponse.redirect(new URL(`/api/auth/login?return_to=${returnTo}`, request.url));
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|_next/webpack|favicon.ico|api/auth).*)'],
};
```

## Step 4 — Verify

After writing all files, check:

- [ ] Salt string in `auth.ts` `deriveKey` matches the one in `middleware.ts` `deriveKey`
- [ ] Import paths in route handlers correctly reach `lib/auth` (adjust `../../../../` for actual depth)
- [ ] `output: "export"` is absent from `next.config.ts`
- [ ] All 5 env vars documented in Step 1 are noted for the user to set in Vercel

## Key design decisions

| Decision | Rationale |
|---|---|
| No MSAL / NextAuth | Zero dependencies, full control, simpler to audit |
| Dual crypto implementations | Middleware runs on Edge (Web Crypto API); route handlers run on Node.js (`node:crypto`). Both produce compatible ciphertext. |
| Encrypted `state` parameter | Carries the return URL encrypted with `SESSION_SECRET` — acts as CSRF protection and preserves deep links |
| No ID token signature verification | `id_token` arrives via server-to-server POST to Azure's token endpoint (back-channel), not from the browser, so it cannot be tampered with |
| 8-hour session cap | `MAX_SESSION_SECONDS` limits sessions regardless of Azure token expiry |
| Dev mode bypass | `NODE_ENV === 'development'` skips auth so local dev works without Azure credentials |
| `sanitizeReturnTo` | Prevents open redirect — requires path starts with `/` and not `//` |
