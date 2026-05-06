#!/usr/bin/env python3
"""
Migration 0001: Initialize migration tracking baseline.

No-op. Establishes the migration chain for workspaces created at R1.
Workspaces installed before migrations existed will run this on their
first update, recording R1 as their baseline.
"""

print("  baseline established (R1)")
