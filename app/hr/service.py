from app.hr.api_client import get_employee, get_manager


def normalize_username(username: str | None) -> str | None:
    if not username or not username.strip() or len(username.strip()) > 128:
        return None
    return username.strip().lower()


def search_employee(username: str) -> dict:
    employee = get_employee(username)
    return {"found": employee is not None, "employee": employee} if employee else {"found": False}


def employee_manager(username: str) -> dict:
    manager = get_manager(username)
    return {"found": manager is not None, "manager": manager.get("manager") if manager else None}


def employee_department(username: str) -> dict:
    employee = get_employee(username)
    return {"found": employee is not None, "department": employee.get("department") if employee else None}
