import re

with open('/usr/local/bin/mb-deploy', 'r') as f:
    content = f.read()

old_code = """    local domain_payload
    domain_payload=$(jq -n \\
        --arg d "https://$DOMAIN" \\
        --arg labels "$labels_b64" \\
        '{ domains: $d, custom_labels: $labels }')"""

new_code = """    local domain_payload
    if [ "$BUILD_PACK" = "dockercompose" ]; then
        domain_payload=$(jq -n \\
            --arg labels "$labels_b64" \\
            '{ custom_labels: $labels }')
    else
        domain_payload=$(jq -n \\
            --arg d "https://$DOMAIN" \\
            --arg labels "$labels_b64" \\
            '{ domains: $d, custom_labels: $labels }')
    fi"""

new_content = content.replace(old_code, new_code)

with open('/usr/local/bin/mb-deploy', 'w') as f:
    f.write(new_content)
