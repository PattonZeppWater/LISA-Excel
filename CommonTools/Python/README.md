# CommonTools — Python

Shared Python utilities for all LISA services. Import via `sys.path.insert` at the top of any service's `app.py` or route file.

## Import Pattern

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..', 'CommonTools', 'Python'))

from file_validation import require_file
from error_helpers import bad_request, server_error
from html_utils import escape_html
from docx_utils import inject_zip_payload, load_zip_payload
```

Adjust the number of `'..'` segments to match the service's depth relative to the repo root.

## Modules

| Module | Exports | Use When |
|---|---|---|
| `file_validation.py` | `validate_extension`, `require_file` | Any route that accepts a file upload |
| `error_helpers.py` | `bad_request`, `not_found`, `unprocessable`, `server_error` | Returning standard error responses |
| `html_utils.py` | `escape_html` | Generating HTML preview content from user data |
| `docx_utils.py` | `inject_zip_payload`, `load_zip_payload` | Embedding/reading JSON metadata inside a DOCX |
