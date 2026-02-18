
import re
import os

file_path = 'public/dashboard.html'

with open(file_path, 'r') as f:
    content = f.read()

# Fix spacing in tags using regex
# < div -> <div
content = re.sub(r'<\s+div', '<div', content)
content = re.sub(r'<\s+/div', '</div', content)
content = re.sub(r'</\s+div', '</div', content)
content = re.sub(r'div\s+>', 'div>', content)

# < i -> <i
content = re.sub(r'<\s+i\s+', '<i ', content)
content = re.sub(r'<\s+/i', '</i', content)
content = re.sub(r'</\s+i', '</i', content)
content = re.sub(r'i\s+>', 'i>', content)

# Fix specific strings
content = content.replace('btn - slides', 'btn-slides')
content = re.sub(r'/\s+api\s+/', '/api/', content)
content = content.replace('generate - slides', 'generate-slides')
content = content.replace('${ renderCategoryColumn', '${renderCategoryColumn')

with open(file_path, 'w') as f:
    f.write(content)

print(f"Fixed dashboard.html. Size: {len(content)}")
