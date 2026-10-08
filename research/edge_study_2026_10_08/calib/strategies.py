"""calib: cost-model calibration role. No trading configurations are proposed by this role.

The deliverable is calibration.py (extra_bps_per_leg, exit_reason_extra_bps, calibrated_net_pct); see REPORT.md.
CONFIGS / BASELINES are intentionally empty so an integrator that loops over every research folder can import
this module without effect.
"""
import sys
sys.path.insert(0, (__import__('os').path.normpath(__import__('os').path.join(__import__('os').path.dirname(__import__('os').path.abspath(__file__)), '..'))) + r'')
import harness as H  # noqa: F401  (required import contract)

CONFIGS = []
BASELINES = []
