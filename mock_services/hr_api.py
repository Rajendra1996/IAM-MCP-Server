"""Small simulated HRIS for the read-only HR MCP server."""

from fastapi import FastAPI, HTTPException

app = FastAPI(title="Demo HR API")
EMPLOYEES = {
    "alice": {"username": "alice", "display_name": "Alice Brown", "department": "Finance", "manager": "jane", "active": True},
    "john": {"username": "john", "display_name": "John Smith", "department": "Engineering", "manager": "taylor", "active": True},
    "bob": {"username": "bob", "display_name": "Bob Wilson", "department": "HR", "manager": "anna", "active": True},
}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/employees/{username}")
def get_employee(username: str):
    employee = EMPLOYEES.get(username.strip().lower())
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee


@app.get("/employees/{username}/manager")
def get_manager(username: str):
    employee = EMPLOYEES.get(username.strip().lower())
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    return {"username": employee["username"], "manager": employee["manager"]}
