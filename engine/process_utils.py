#!/usr/bin/env python3
"""Utilitarios de processo seguros e portaveis.

``os.kill(pid, 0)`` e um teste de existencia comum em POSIX, mas nao e um
probe seguro no Windows. Neste modulo, Windows usa apenas handles de consulta/
espera e nunca envia sinal ao processo observado.
"""
from __future__ import annotations

import os


def _rodando_no_windows():
    return os.name == "nt"


def _pid_ativo_posix(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, OverflowError, ValueError):
        return False


def _pid_ativo_windows(pid):
    """Consulta o estado de ``pid`` sem sinalizar nem terminar o processo.

    Falhas de permissao/consulta sao tratadas de forma conservadora como
    "possivelmente ativo". Isso evita liberar leases ou recuperar jobs que
    ainda podem estar em execucao.
    """
    try:
        import ctypes
        from ctypes import wintypes

        synchronize = 0x00100000
        wait_object_0 = 0x00000000
        wait_timeout = 0x00000102
        error_access_denied = 5
        error_invalid_parameter = 87
        error_not_found = 1168

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            erro = ctypes.get_last_error()
            if erro == error_access_denied:
                return True
            if erro in (error_invalid_parameter, error_not_found):
                return False
            return True

        try:
            resultado = kernel32.WaitForSingleObject(handle, 0)
            if resultado == wait_timeout:
                return True
            if resultado == wait_object_0:
                return False
            return True
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        # Um probe inconclusivo nunca deve virar autorizacao para remover o
        # estado de outro processo. Expiracao/heartbeat continuam como fallback.
        return True


def pid_ativo(pid):
    """Retorna se um PID aparenta estar ativo sem causar efeitos colaterais."""
    try:
        pid = int(pid)
    except (TypeError, ValueError, OverflowError):
        return False
    if pid <= 0:
        return False

    # Alem de ser mais barato, este fast-path impede que uma regressao no
    # backend de plataforma transforme um health check em autoencerramento.
    if pid == os.getpid():
        return True

    if _rodando_no_windows():
        return _pid_ativo_windows(pid)
    return _pid_ativo_posix(pid)
