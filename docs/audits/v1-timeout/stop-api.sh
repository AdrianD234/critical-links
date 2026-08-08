#!/usr/bin/env bash
pkill -f "ui_fixture.py serve" 2>/dev/null || true
sleep 1
echo stopped
