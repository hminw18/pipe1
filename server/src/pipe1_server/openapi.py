"""Print the API's OpenAPI schema (used to generate the frontend's TypeScript types)."""

import json

from pipe1_server.main import app

if __name__ == "__main__":
    print(json.dumps(app.openapi(), ensure_ascii=False, indent=1, sort_keys=True))
