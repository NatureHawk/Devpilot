"""Data access layer.

Each module here owns the SQL for one aggregate. Keeping queries out of route
handlers means the statements stay visible and testable in one place, rather
than being spread across the API surface.
"""
