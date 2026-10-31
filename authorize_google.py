from app.calendar import authorize_oauth
from app.config import load_settings


def main() -> None:
    settings = load_settings()
    if not settings.google_oauth_client_file or not settings.google_oauth_token_file:
        raise RuntimeError("GOOGLE_OAUTH_CLIENT_FILE and GOOGLE_OAUTH_TOKEN_FILE are required")
    authorize_oauth(settings.google_oauth_client_file, settings.google_oauth_token_file)
    print("Google Calendar authorization: OK")


if __name__ == "__main__":
    main()
