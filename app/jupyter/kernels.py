from __future__ import annotations

from fastapi import HTTPException
from jupyter_client.kernelspec import KernelSpecManager

from .environment import prepare_jupyter_environment


class KernelCatalog:
    def __init__(self) -> None:
        self._kernel_spec_manager = KernelSpecManager()

    def available_kernels(self) -> list[str]:
        prepare_jupyter_environment()
        return sorted(self._kernel_spec_manager.find_kernel_specs())

    def default_kernel(self) -> str:
        kernels = self.available_kernels()
        if "python3" in kernels:
            return "python3"
        if not kernels:
            raise HTTPException(status_code=503, detail="no Jupyter kernels are available")
        return kernels[0]
