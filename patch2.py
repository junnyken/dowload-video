import re

with open('/usr/local/bin/mb-deploy', 'r') as f:
    content = f.read()

old_code = """        api_call -X POST "$GITLAB_URL/api/v4/projects/$(urlencode "$GITLAB_PATH")/deploy_keys" \\
            -H "PRIVATE-TOKEN: $GITLAB_TOKEN" \\
            -H "Content-Type: application/json" \\
            -d "$dk_payload" >/dev/null 2>&1 || echo "  (key may already exist — OK)\""""

new_code = """        curl -sS -X POST "$GITLAB_URL/api/v4/projects/$(urlencode "$GITLAB_PATH")/deploy_keys" \\
            -H "PRIVATE-TOKEN: $GITLAB_TOKEN" \\
            -H "Content-Type: application/json" \\
            -d "$dk_payload" >/dev/null 2>&1 || echo "  (key may already exist — OK)\""""

new_content = content.replace(old_code, new_code)

with open('/usr/local/bin/mb-deploy', 'w') as f:
    f.write(new_content)
