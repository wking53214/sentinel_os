"""
Runs the Fortress kernel standalone (no GSA wrapper): 20 runs of the 60-step
simulation under noisier conditions, bucketed by final distortion score.
Reconstructed from artifact_1.py's dual_phase_stress_test().
"""

from sage_k.kernel import Fortress


def dual_phase_stress_test() -> None:
    simulation_instance = Fortress(operational_seed=42)
    green_frequency_count = 0
    yellow_frequency_count = 0
    red_frequency_count = 0

    for _ in range(20):
        evaluation_results = simulation_instance.run_cycle(noise_scale_coefficient=7.0)
        distortion_score = evaluation_results["distortion"]
        if distortion_score < 0.25:
            green_frequency_count += 1
        elif distortion_score < 0.55:
            yellow_frequency_count += 1
        else:
            red_frequency_count += 1

    print("\n=== DUAL PHASE RESULTS ===")
    print(f"GREEN : {green_frequency_count}")
    print(f"YELLOW: {yellow_frequency_count}")
    print(f"RED   : {red_frequency_count}")


if __name__ == "__main__":
    dual_phase_stress_test()
