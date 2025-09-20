import os

import dotenv


dotenv.load_dotenv()


CWM_LOG_LEVEL = os.getenv("CWM_LOG_LEVEL", "DEBUG")
CWM_ENV_TYPE = os.getenv("CWM_ENV_TYPE")

NAMESPACE = os.getenv("NAMESPACE", "default")
IS_PRIMARY = os.getenv("IS_PRIMARY", "") == "true"
ALLOWED_PRIMARY_KEY = os.getenv("ALLOWED_PRIMARY_KEY", "")
