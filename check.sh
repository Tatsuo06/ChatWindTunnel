#!/bin/bash

ps -Ao pid,command | grep -E "ChatTowingTank|ChatWindTunnel" | grep -E "uvicorn|streamlit" | grep -v grep
