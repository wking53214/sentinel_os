#!/bin/bash

set -e

echo "========================================================================"
echo "SENTINEL OS CLEANUP - REMOVING DEAD CODE"
echo "========================================================================"
echo ""
echo "This script will delete old/experimental code that is no longer used."
echo "Everything is committed to GitHub, so nothing is truly lost."
echo ""
echo "Press ENTER to continue, or Ctrl+C to abort."
read

# Count files to be deleted
echo ""
echo "[1/5] Identifying files to delete..."
count=0

# Delete old layers
echo "[2/5] Deleting old Iceburg layer..."
rm -rf Admin/
rm -rf API/
rm -f CLI/CLI.py
echo "  ✓ Removed: Admin/, API/, CLI/"
((count+=3))

# Delete old orchestrators
echo "[3/5] Deleting old orchestrators..."
rm -f iceberg_orchestrator.py
rm -f iceberg_final_orchestrator.py
rm -f integration_adaptive.py
rm -f Main.py
echo "  ✓ Removed: old orchestrators"
((count+=4))

# Delete experimental RL
echo "[4/5] Deleting experimental RL engines..."
rm -f Engines/rl_ppo.py
rm -f Engines/rl_ppo_v2.py
rm -f Engines/rl_marl.py
rm -f Engines/staffing_rl.py
rm -f Engines/ppo_trainer.py
rm -f Engines/bayes_gpu.py
echo "  ✓ Removed: rl_ppo.py, rl_ppo_v2.py, rl_marl.py, staffing_rl.py, ppo_trainer.py, bayes_gpu.py"
((count+=6))

# Delete experiments
echo "[5/5] Deleting experimental infrastructure..."
rm -rf Replay/
rm -rf SDK/
rm -rf Registry/
rm -f Telemetry/aggregator.py
rm -f core/resilience_config.py
rm -f api_server.py
echo "  ✓ Removed: Replay/, SDK/, Registry/, Telemetry/aggregator.py, core/resilience_config.py, api_server.py"
((count+=6))

# Delete old tests
echo "[6/6] Deleting orphaned tests..."
rm -f Tests/test_rl_ppo.py
rm -f Tests/test_rl_marl.py
rm -f Tests/test_staffing_rl.py
rm -f Tests/test_gpu_bayes.py
rm -f Tests/test_replay_engine.py
rm -f Tests/test_simulator_core.py
rm -f Tests/test_cluster_runner.py
rm -f Tests/test_latent.py
rm -f Tests/test_latent_consolidated.py
rm -f Tests/test_latent_regression.py
rm -f Tests/test_api_contract.py
echo "  ✓ Removed: 11 orphaned test files"
((count+=11))

echo ""
echo "========================================================================"
echo "CLEANUP COMPLETE"
echo "========================================================================"
echo ""
echo "Deleted $count files and folders"
echo ""
echo "Next steps:"
echo "  1. Verify nothing broke: python3 -m pytest Tests/ -v"
echo "  2. Commit to GitHub: cd .. && git add -A && git commit -m 'Clean up: remove dead code layers'"
echo "  3. Push: git push"
echo ""
