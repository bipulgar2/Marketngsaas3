
import os
try:
    files = os.listdir('.')
    with open('dir_listing.txt', 'w') as f:
        f.write('\n'.join(files))
except Exception as e:
    with open('dir_listing_error.txt', 'w') as f:
        f.write(str(e))
