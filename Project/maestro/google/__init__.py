"""Google Gmail + Drive for MAESTRO (optional extra: `pip install -e ".[google]"`).

`auth` owns sign-in and the token; `api` is the only code that talks to Google.
The verbs that use them live in `maestro/executor/google.py`, inside the same
closed registry, scorer and consent gate as every other verb.
"""
