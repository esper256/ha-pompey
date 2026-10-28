"""Read effective required checks with the workflow token's metadata permission."""

from dependency_updates import api


def protection_from_rules(rules):
    # This endpoint returns only active rules applicable to the named branch.
    # The required context and its Actions app must occur in the same strict rule.
    for rule in rules:
        if rule.get("type") != "required_status_checks":
            continue
        parameters = rule.get("parameters", {})
        checks = [{"context": check.get("context"), "app_id": check.get("integration_id")}
                  for check in parameters.get("required_status_checks", [])]
        if (parameters.get("strict_required_status_checks_policy") is True
                and {"context": "CI required", "app_id": 15368} in checks):
            return {"strict": True, "checks": checks}
    raise ValueError("Active strict GitHub Actions CI protection must remain enabled on main")


def required_protection(repo):
    return protection_from_rules(api("repos/" + repo + "/rules/branches/main?per_page=100"))
