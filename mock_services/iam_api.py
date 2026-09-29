"""Demo IAM REST backend. Only this service reads the example dictionaries."""

from fastapi import FastAPI, HTTPException

from mock_services.data import APPLICATIONS, USERS

app = FastAPI(title="IAM Demo REST API")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/users/{username}")
def get_user(username: str):
    user = USERS.get(username.strip().lower())
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@app.get("/applications/{app_name}")
def get_application(app_name: str):
    application = APPLICATIONS.get(app_name.strip().lower())
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return application
