#!/usr/bin/env python3
"""Deep-merges the dotfiles Claude Code settings template into the live settings.json.

Structural keys (env, permissions, hooks, statusLine, enabledPlugins,
extraKnownMarketplaces, $schema) are dotfiles-managed and enforced/unioned on every
run. Everything else is seeded once, then left alone -- Claude Code and
`claude plugin install` rewrite scalar preferences (model, effortLevel,
feedbackSurveyState, ...) into this file at runtime, and resyncing dotfiles should
not stomp on that.
"""
import json
import re
import sys

OVERWRITE_KEYS = {"statusLine", "$schema"}

# Write(<path>) rules are never matched by Claude Code's file permission checks --
# only Edit(<path>) rules cover file-writing tools. Normalize any stale Write(path)
# rule (ours or one added by hand) to its Edit(path) equivalent so it actually works.
WRITE_PATH_RULE = re.compile(r"^Write\((.+)\)$")

# Hook groups invoking one of these scripts are dotfiles-owned: we ship the
# script, so we own how it is invoked. They are dropped from the live file
# before the template's groups are appended, making the template authoritative
# rather than additive. Without this, changing a hook's command line leaves the
# old group in place and adds the new one beside it -- which is how an
# already-provisioned workspace ended up running a `python <script>` Stop hook
# (no `python` on the image, python3 only) on every turn even after the
# template was fixed. Matched on script name, not full command, so the same
# group is recognised across command-line rewrites.
DOTFILES_OWNED_HOOK_SCRIPTS = ("langfuse_hook.py",)


def normalize_permission_rule(rule):
    match = WRITE_PATH_RULE.match(rule)
    return f"Edit({match.group(1)})" if match else rule


def merge_str_list(existing, template, normalize=None):
    seen = set()
    result = []
    for item in list(template) + list(existing):
        if normalize:
            item = normalize(item)
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def merge_permissions(existing, template):
    result = dict(existing)
    for key, template_list in template.items():
        result[key] = merge_str_list(
            existing.get(key, []), template_list, normalize=normalize_permission_rule
        )
    return result


def is_dotfiles_owned_hook_group(group):
    return any(
        script in hook.get("command", "")
        for hook in group.get("hooks", [])
        for script in DOTFILES_OWNED_HOOK_SCRIPTS
    )


def merge_hooks(existing, template):
    # Prune across every event in the live file, not just the ones the template
    # still declares, so retiring a hook actually removes it.
    result = {
        event: [g for g in groups if not is_dotfiles_owned_hook_group(g)]
        for event, groups in existing.items()
    }
    for event, template_groups in template.items():
        existing_groups = result.get(event, [])
        seen = {json.dumps(g, sort_keys=True) for g in existing_groups}
        merged = list(existing_groups)
        for group in template_groups:
            key = json.dumps(group, sort_keys=True)
            if key not in seen:
                seen.add(key)
                merged.append(group)
        result[event] = merged
    # An event left with no groups is noise; drop the key entirely.
    return {event: groups for event, groups in result.items() if groups}


def merge_plain_dict(existing, template):
    result = dict(existing)
    result.update(template)
    return result


def main():
    rendered_template_path, target_path = sys.argv[1], sys.argv[2]

    with open(rendered_template_path) as f:
        template = json.load(f)

    try:
        with open(target_path) as f:
            existing = json.load(f)
    except FileNotFoundError:
        existing = {}

    result = dict(existing)
    for key, template_value in template.items():
        if key in OVERWRITE_KEYS:
            result[key] = template_value
        elif key == "env":
            result[key] = merge_plain_dict(existing.get(key, {}), template_value)
        elif key == "permissions":
            result[key] = merge_permissions(existing.get(key, {}), template_value)
        elif key == "hooks":
            result[key] = merge_hooks(existing.get(key, {}), template_value)
        elif key in ("enabledPlugins", "extraKnownMarketplaces"):
            result[key] = merge_plain_dict(existing.get(key, {}), template_value)
        elif key not in existing:
            result[key] = template_value

    with open(target_path, "w") as f:
        json.dump(result, f, indent=2)
        f.write("\n")


if __name__ == "__main__":
    main()
