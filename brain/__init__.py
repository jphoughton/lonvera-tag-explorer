"""The Lonvera brain: read-only tools that tell an agent what data exists, what each metric means, and let it query the analytics views.

Two front ends share this code: the hosted endpoint /mcp on the web app (app/brain_mcp.py) and a local stdio server (python -m brain.server).
"""
