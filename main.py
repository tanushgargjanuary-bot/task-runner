import sys
from engine import sync_metrics, update_weights, process_queue

def main():
    print("[RUN] Executing job worker...")
    state = sync_metrics()
    weights = update_weights(state)
    process_queue(weights)
    print("[RUN] Execution complete.")

if __name__ == "__main__":
    main()
