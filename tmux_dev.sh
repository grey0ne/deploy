#!/bin/bash
# Usage: tmux_dev.sh <component>...  (dc passes the enabled components from deploy.json)
SESSION="${PROJECT_NAME}dev"
COMPONENTS=("$@")

tmux kill-session -t $SESSION
tmux new-session -d -s $SESSION

tmux new-window -t $SESSION:1 -n 'Server'
tmux kill-window -t $SESSION:0

# One pane per enabled component plus the balancer.
tmux send-keys "pc nginx logs nginx -f" C-m
for component in "${COMPONENTS[@]}"; do
    tmux split-window -v
    tmux send-keys "pc logs $component -f" C-m
done
tmux select-layout tiled

tmux attach -t $SESSION
