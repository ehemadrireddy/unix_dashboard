import json
import os
from datetime import datetime, timedelta
from typing import List, Optional

import pandas as pd
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Header, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from filelock import FileLock
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

# Load Environment Variables
load_dotenv()

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "fallback-secret")
INGEST_API_KEY = os.getenv("INGEST_API_KEY", "fallback-ingest-key")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 60))

DATA_DIR = "data"
USERS_FILE = f"{DATA_DIR}/users.json"
SCHEMA_FILE = f"{DATA_DIR}/schema.json"
SERVER_DATA_FILE = f"{DATA_DIR}/server_data.json"

os.makedirs(DATA_DIR, exist_ok=True)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")

app = FastAPI(title="Infrastructure Dashboard API")

# --- MODELS ---
class User(BaseModel):
    username: str
    role: str
    disabled: bool = False

class Token(BaseModel):
    access_token: str
    token_type: str

class ManualUpdate(BaseModel):
    field: str
    value: str

# --- HELPERS ---
def load_json(filepath):
    with FileLock(f"{filepath}.lock"):
        with open(filepath, 'r') as f:
            return json.load(f)

def save_json(filepath, data):
    with FileLock(f"{filepath}.lock"):
        temp_filepath = f"{filepath}.tmp"
        with open(temp_filepath, 'w') as f:
            json.dump(data, f, indent=2)
        os.replace(temp_filepath, filepath)

# --- AUTH & RBAC ---
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(status_code=401, detail="Could not validate credentials")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None: raise credentials_exception
    except JWTError: raise credentials_exception
    
    users = load_json(USERS_FILE)
    user_dict = next((u for u in users if u["username"] == username), None)
    if user_dict is None: raise credentials_exception
    return User(**user_dict)

def require_role(allowed_roles: List[str]):
    def role_checker(current_user: User = Depends(get_current_user)):
        if current_user.role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Operation not permitted")
        return current_user
    return role_checker

# --- ENDPOINTS ---
@app.post("/api/auth/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    users = load_json(USERS_FILE)
    user_dict = next((u for u in users if u["username"] == form_data.username), None)
    if not user_dict or not verify_password(form_data.password, user_dict["hashed_password"]):
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    access_token = create_access_token(data={"sub": user_dict["username"], "role": user_dict["role"]})
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/api/schema")
async def get_schema(current_user: User = Depends(require_role(["R", "W", "E"]))):
    return load_json(SCHEMA_FILE)

@app.post("/api/schema")
async def add_dynamic_header(field: str, header: str, type: str, current_user: User = Depends(require_role(["E"]))):
    schema = load_json(SCHEMA_FILE)
    if any(col['field'] == field for col in schema):
        raise HTTPException(status_code=400, detail="Field already exists")
    schema.append({"field": field, "header": header, "type": type, "editable": (type == "manual")})
    save_json(SCHEMA_FILE, schema)
    server_data = load_json(SERVER_DATA_FILE)
    for server in server_data: server[field] = None
    save_json(SERVER_DATA_FILE, server_data)
    return {"status": "success"}

@app.get("/api/servers")
async def get_servers(global_search: Optional[str] = None, current_user: User = Depends(require_role(["R", "W", "E"]))):
    data = load_json(SERVER_DATA_FILE)
    df = pd.DataFrame(data)
    if df.empty: return {"data": [], "total": 0}
    if global_search:
        mask = df.astype(str).apply(lambda x: x.str.contains(global_search, case=False, na=False)).any(axis=1)
        df = df[mask]
    return {"data": df.to_dict(orient="records"), "total": len(df)}

@app.patch("/api/servers/{hostname}")
async def update_manual_field(hostname: str, update: ManualUpdate, current_user: User = Depends(require_role(["W", "E"]))):
    schema = load_json(SCHEMA_FILE)
    field_def = next((col for col in schema if col['field'] == update.field), None)
    if not field_def or field_def['type'] != 'manual':
        raise HTTPException(status_code=400, detail="Cannot edit auto-fetched field")
    server_data = load_json(SERVER_DATA_FILE)
    for server in server_data:
        if server.get("hostname") == hostname:
            server[update.field] = update.value
            break
    save_json(SERVER_DATA_FILE, server_data)
    return {"status": "success"}

@app.post("/api/internal/ingest")
async def receive_collector_data(payload: dict, x_ingest_key: str = Header(...)):
    if x_ingest_key != INGEST_API_KEY: raise HTTPException(status_code=403, detail="Invalid Key")
    incoming_servers = payload.get("servers", [])
    schema = load_json(SCHEMA_FILE)
    auto_fields = [col['field'] for col in schema if col['type'] == 'auto']
    server_data = load_json(SERVER_DATA_FILE)
    server_dict = {s['hostname']: s for s in server_data}
    for incoming in incoming_servers:
        hostname = incoming.get("hostname")
        if hostname in server_dict:
            for field in auto_fields:
                if field in incoming: server_dict[hostname][field] = incoming[field]
        else:
            new_server = {"hostname": hostname}
            for col in schema: new_server[col['field']] = incoming.get(col['field']) if col['type'] == 'auto' else None
            server_dict[hostname] = new_server
    save_json(SERVER_DATA_FILE, list(server_dict.values()))
    return {"status": "success"}

@app.get("/api/export")
async def export_data(global_search: Optional[str] = None, current_user: User = Depends(require_role(["R", "W", "E"]))):
    data = load_json(SERVER_DATA_FILE)
    df = pd.DataFrame(data)
    if global_search:
        mask = df.astype(str).apply(lambda x: x.str.contains(global_search, case=False, na=False)).any(axis=1)
        df = df[mask]
    return StreamingResponse(iter([df.to_csv(index=False)]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=export.csv"})

# --- STARTUP (Initialize Data Files) ---
@app.on_event("startup")
def startup_event():
    if not os.path.exists(USERS_FILE):
        save_json(USERS_FILE, [{"username": "admin", "hashed_password": get_password_hash("admin123"), "role": "E", "disabled": False}])
    if not os.path.exists(SCHEMA_FILE):
        save_json(SCHEMA_FILE, [
            {"field": "hostname", "header": "Hostname", "type": "auto", "editable": False},
            {"field": "os_type", "header": "OS", "type": "auto", "editable": False},
            {"field": "environment", "header": "Environment", "type": "manual", "editable": True}
        ])
    if not os.path.exists(SERVER_DATA_FILE):
        save_json(SERVER_DATA_FILE, [])

# --- SERVE FRONTEND ---
# This ensures FastAPI serves the React app directly
app.mount("/assets", StaticFiles(directory="dist/assets"), name="assets")

@app.get("/{full_path:path}")
async def serve_react(full_path: str):
    return FileResponse("dist/index.html")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8443)