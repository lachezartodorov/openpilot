#!/usr/bin/env python3
from openpilot.common.numpy_fast import interp
from openpilot.common.params import Params

def main():
    params = Params()

    # 1. Get the raw value (Index 0-12)
    # The Params class returns bytes, so we decode to utf8, then cast to int.
    raw_value = params.get("MaxTimeOffroad", encoding="utf8")

    if raw_value is None:
        print("Error: Could not read 'MaxTimeOffroad' from Params.")
        return

    try:
        idx = int(raw_value)
    except ValueError:
        print(f"Error: Parameter value is not an integer: {raw_value}")
        return

    # 2. Define the Mapping Table (Copied from your snippet)
    # 0=0s, 1=5s, ... 12=30 hours (108000s)
    x_points = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    y_values = [0, 5, 30, 60, 180, 300, 600, 1800, 3600, 10800, 18000, 36000, 108000]

    # 3. Calculate the actual time in seconds
    max_time_offroad_s = interp(idx, x_points, y_values)

    # 4. Print Results
    print(f"Raw Param Index: {idx}")
    print(f"Max Offroad Time: {max_time_offroad_s:.0f} seconds")
    print(f"Max Offroad Time: {max_time_offroad_s / 3600:.2f} hours")

if __name__ == "__main__":
    main()