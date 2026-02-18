
import os

file_path = 'public/dashboard.html'

with open(file_path, 'r') as f:
    content = f.read()

# Fix common issues introduced
replacements = [
    ('< div', '<div'),
    ('</ div', '</div'),
    ('< /div', '</div'),
    ('< / div', '</div'),
    ('div >', 'div>'),
    ('< i ', '<i '),
    ('</ i', '</i'),
    ('< / i', '</i'),
    ('i >', 'i>'),
    ('btn - slides', 'btn-slides'),
    ('/ api /', '/api/'),
    ('generate - slides', 'generate-slides'),
    ('` i', '`i'), # Fixing weird backtick spacing if any
    ('` <', '`<'),
]

for old, new in replacements:
    content = content.replace(old, new)

with open(file_path, 'w') as f:
    f.write(content)

print("Dashboard HTML fixed.")
