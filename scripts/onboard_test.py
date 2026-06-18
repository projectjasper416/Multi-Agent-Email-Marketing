"""
Onboard the example "TaskFlow" customer for local testing (see TEST_DATA.md).

Runs the three setup steps from onboarding/setup_flow.py end to end and
auto-approves every behavioral signal the Schema Intelligence Agent proposes
(in a real setup a human does the yes/no review — here we approve all so a
planning run has a full reasoning vocabulary).

Prerequisites:
  * migrations/001..003 applied + scripts/seed_test_data.sql loaded
  * python -m scripts.db_setup   (creates Store + checkpointer tables)
  * .env has SUPABASE_*, SUPABASE_DB_URL, ANTHROPIC_API_KEY, CUSTOMER_ID=taskflow

Run:
    python -m scripts.onboard_test
"""
from __future__ import annotations

import asyncio

import config
from onboarding import setup_flow


# Raw onboarding-form inputs Claude synthesizes into product_context.md.
FORM_INPUTS = {
    "product_name": "TaskFlow",
    "one_liner": "A freemium project-management SaaS for small teams and solo pros.",
    "core_action": "Creating and completing tasks inside projects.",
    "free_vs_premium": (
        "Free accounts are capped at 3 active (non-archived) projects and have no "
        "integrations. Pro/Business unlock unlimited projects + integrations "
        "(Slack, GitHub, Zapier). A user is premium iff they have a row in "
        "`subscriptions` with status='active'; absence of a row means free."
    ),
    "brand_tone": "Warm, concise, practical. Like a helpful teammate, never salesy. No hype.",
    "email_hard_rules": (
        "Never email premium (active-subscription) users, opted-out users "
        "(users.marketing_opt_out=true), or soft-deleted users "
        "(users.deleted_at is not null). Respect the 4-day frequency cap across "
        "both the agent log and the legacy `legacy_email_sends` table."
    ),
    "exclusion_rules": "Premium, unsubscribed, soft-deleted, <1 day old, emailed in last 4 days.",
    "deep_links": {
        "project": "https://app.taskflow.com/p/{project_id}",
        "new_task": "https://app.taskflow.com/new",
        "upgrade": "https://app.taskflow.com/upgrade",
    },
    "extra_context": (
        "users.last_active_at = last app session. preferences is JSONB; "
        "preferences.onboarding_completed=false flags users who never set up."
    ),
}

EMAIL_SETTINGS = {
    "from_address": "hello@taskflow.com",
    "brand_name": "TaskFlow",
    "unsubscribe_base_url": "https://app.taskflow.com/unsubscribe",
}


async def main() -> None:
    customer_id = config.CUSTOMER_ID or "taskflow"
    print(f"Onboarding customer_id={customer_id!r}\n")

    print("Step 1/3  generate_brief — synthesizing product_context.md ...")
    brief = await setup_flow.generate_brief(customer_id, FORM_INPUTS, EMAIL_SETTINGS)
    print(brief[:600] + ("..." if len(brief) > 600 else ""))
    print()

    print("Step 2/3  analyze_schema — Schema Intelligence Agent introspecting DB ...")
    raw_signals = await setup_flow.analyze_schema(customer_id)
    print(f"  proposed {len(raw_signals)} signals:")
    for s in raw_signals:
        print(f"    - {s.get('name')}: {s.get('description')}")
    print()

    print("Step 3/3  save_approved_signals — auto-approving ALL proposed signals ...")
    approvals = [{**s, "approved": True} for s in raw_signals]
    approved = await setup_flow.save_approved_signals(customer_id, approvals)
    print(f"  approved {len(approved)} signals. Setup complete.")
    print("\nNext: run `python handler_plan.py` for a planning dry run.")


if __name__ == "__main__":
    asyncio.run(main())
