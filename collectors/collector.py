import asyncio
import asyncssh
import json
import os
import sys
import httpx
from datetime import datetime

# ==========================================
# 1. CONFIGURATION (Loaded from collector.env)
# ==========================================
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "https://dashboard-server:8443/api/internal/ingest")
INGEST_API_KEY = os.getenv("INGEST_API_KEY", "YOUR_INTERNAL_INGEST_API_KEY")
COLLECTOR_ID = os.getenv("COLLECTOR_ID", "admin-server-unknown")

# Default SSH settings (overridden if specified in targets.json)
DEFAULT_SSH_USER = os.getenv("DEFAULT_SSH_USER", "dash_fetcher")
DEFAULT_SSH_PORT = int(os.getenv("DEFAULT_SSH_PORT", 22))
SSH_TIMEOUT = int(os.getenv("SSH_TIMEOUT", 15))

# Local file paths
TARGETS_FILE = "targets.json"
COMMAND_LIBRARY_FILE = "command_library.json"

# ==========================================
# 2. HELPER FUNCTIONS
# ==========================================
def load_json(filepath):
    """Safely loads a JSON file."""
    if not os.path.exists(filepath):
        print(f"Error: {filepath} not found. Exiting.")
        sys.exit(1)
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {filepath}: {e}")
        sys.exit(1)

async def fetch_server_data(target, command_library):
    """
    Connects to a single server, detects OS, runs commands, and returns data.
    Supports target as 'ip' or 'username@ip'.
    """
    # Parse target string
    if "@" in target:
        username, host = target.split("@")
    else:
        username = DEFAULT_SSH_USER
        host = target

    try:
        async with asyncssh.connect(
            host, 
            username=username, 
            port=DEFAULT_SSH_PORT, 
            known_hosts=None, 
            connect_timeout=SSH_TIMEOUT
        ) as conn:
            
            # 1. Detect OS Type
            uname_result = await conn.run("uname -s", check=True)
            uname_output = uname_result.stdout.strip().lower()
            
            if "linux" in uname_output:
                os_type = "RHEL"
            elif "aix" in uname_output:
                os_type = "AIX"
            else:
                return {"hostname": host, "error": f"Unsupported OS: {uname_output}"}
            
            server_data = {"hostname": host, "os_type": os_type}
            
            # 2. Run commands based on OS Type
            for field, commands in command_library.items():
                if os_type in commands and commands[os_type]:
                    cmd = commands[os_type]
                    try:
                        result = await conn.run(cmd, check=True)
                        server_data[field] = result.stdout.strip()
                    except Exception as e:
                        server_data[field] = None # Command failed or not found
                else:
                    # If OS is not provided for this field, ignore it (as requested)
                    pass
            
            return server_data
            
    except asyncssh.misc.PermissionDenied:
        return {"hostname": host, "error": "SSH Permission Denied (Check SSH keys)"}
    except asyncssh.misc.ConnectionLost:
        return {"hostname": host, "error": "SSH Connection Lost"}
    except asyncio.TimeoutError:
        return {"hostname": host, "error": "SSH Connection Timeout"}
    except Exception as e:
        return {"hostname": host, "error": f"Error: {str(e)}"}

# ==========================================
# 3. MAIN EXECUTION
# ==========================================
async def main():
    print(f"[{datetime.now()}] Starting data collection for {COLLECTOR_ID}...")
    
    # 1. Load Configurations
    targets = load_json(TARGETS_FILE)
    command_library = load_json(COMMAND_LIBRARY_FILE)
    
    if not targets:
        print("No targets found in targets.json. Exiting.")
        return

    print(f"Loaded {len(targets)} targets and {len(command_library)} command fields.")

    # 2. Fetch data in parallel (AsyncIO)
    tasks = [fetch_server_data(target, command_library) for target in targets]
    results = await asyncio.gather(*tasks)
    
    # 3. Separate successful and failed servers
    successful_servers = [r for r in results if "error" not in r]
    failed_servers = [r for r in results if "error" in r]
    
    if failed_servers:
        print(f"\nWarning: Failed to fetch data from {len(failed_servers)} servers.")
        for f in failed_servers:
            print(f"  - {f['hostname']}: {f['error']}")

    if not successful_servers:
        print("\nNo successful data to push. Exiting.")
        return

    # 4. Prepare Payload for Server 1
    payload = {
        "collector_id": COLLECTOR_ID,
        "timestamp": datetime.utcnow().isoformat(),
        "servers": successful_servers
    }
    
    # 5. Push to Dashboard Server
    print(f"\nPushing data for {len(successful_servers)} servers to {DASHBOARD_URL}...")
    
    # verify=False is used because internal servers often use self-signed certs. 
    # In highly secure environments, set verify=True and provide the CA bundle path.
    async with httpx.AsyncClient(verify=False, timeout=60.0) as client:
        try:
            response = await client.post(
                DASHBOARD_URL,
                json=payload,
                headers={"X-Ingest-Key": INGEST_API_KEY}
            )
            if response.status_code == 200:
                print(f"Success: {response.json()}")
            else:
                print(f"Error: Dashboard returned {response.status_code} - {response.text}")
        except httpx.ConnectError:
            print(f"Error: Could not connect to Dashboard at {DASHBOARD_URL}")
        except httpx.TimeoutException:
            print("Error: Request to Dashboard timed out.")
        except Exception as e:
            print(f"Failed to push data: {str(e)}")

if __name__ == "__main__":
    asyncio.run(main())