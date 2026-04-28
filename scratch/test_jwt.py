import os
import jwt
from pathlib import Path
from dotenv import load_dotenv

# Path to the .env file in the root
env_path = Path("/Users/akashdubey/Documents/CodingProjects/RAIL/RutgersAgenticIntelligenceLabs/.env")
load_dotenv(env_path)

private_key = os.getenv("GITHUB_APP_PRIVATE_KEY")
print(f"Original key start: {repr(private_key[:50])}")

# Try to encode a simple JWT
try:
    payload = {"test": "data"}
    encoded = jwt.encode(payload, private_key, algorithm="RS256")
    print("Success without unescaping!")
except Exception as e:
    print(f"Failed without unescaping: {e}")

# Try with unescaping
try:
    unescaped_key = private_key.replace("\\n", "\n")
    print(f"Unescaped key start: {repr(unescaped_key[:50])}")
    encoded = jwt.encode(payload, unescaped_key, algorithm="RS256")
    print("Success WITH unescaping!")
except Exception as e:
    print(f"Failed WITH unescaping: {e}")
