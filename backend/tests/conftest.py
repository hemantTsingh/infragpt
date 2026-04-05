"""
Stub out the `kubernetes` SDK at the sys.modules level so that
k8s_client._load_config() (called at module import time) never
tries to contact a real cluster during CI test runs.
"""
import sys
from unittest.mock import MagicMock

# Build a minimal mock hierarchy that satisfies all import paths used
# by integrations/k8s_client.py before any test module is imported.
_k8s = MagicMock()
_k8s.config.ConfigException = Exception
_k8s.config.load_incluster_config = MagicMock()
_k8s.config.load_kube_config = MagicMock()

sys.modules.setdefault("kubernetes", _k8s)
sys.modules.setdefault("kubernetes.client", _k8s.client)
sys.modules.setdefault("kubernetes.client.rest", _k8s.client.rest)
sys.modules.setdefault("kubernetes.config", _k8s.config)
