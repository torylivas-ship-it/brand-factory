from fastapi import HTTPException, status

PLAN_HIERARCHY = {"free": 0, "starter": 1, "growth": 2, "agency": 3}


def check_plan(required_plan: str, user_plan: str) -> None:
    """
    Raises HTTP 403 if user_plan is below required_plan.
    Plans: free < starter < growth < agency
    """
    required_level = PLAN_HIERARCHY.get(required_plan, 0)
    user_level = PLAN_HIERARCHY.get(user_plan, 0)

    if user_level < required_level:
        upgrade_target = required_plan.capitalize()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "plan_required",
                "message": f"This feature requires a {upgrade_target} plan.",
                "required_plan": required_plan,
                "current_plan": user_plan,
            },
        )
