import os
from supabase import create_client, Client

_admin_client: Client | None = None


def get_supabase_admin() -> Client:
    """Returns a singleton Supabase client using the service role key (bypasses RLS)."""
    global _admin_client
    if _admin_client is None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_KEY")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")
        _admin_client = create_client(url, key)
    return _admin_client
