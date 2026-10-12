"""Linux USB access through PyUSB/libusb; restore the HID driver on close."""

import fcntl
import hashlib
import os
import time
from pathlib import Path

DESCRIPTOR_HASH = "fa85f6013264f197b61acda0e51777d56a6776c4f7c5bc3459deff0a051f8c14"


def find_monitor() -> tuple[int, int]:
    candidates = []
    for device in Path("/sys/bus/usb/devices").iterdir():
        try:
            if (device / "idVendor").read_text().strip() != "1462":
                continue
            if (device / "idProduct").read_text().strip() != "3fa4":
                continue
            descriptors = list(
                (device / f"{device.name}:1.0").glob("*/report_descriptor")
            )
            if len(descriptors) != 1:
                raise RuntimeError("cannot verify the monitor HID descriptor")
            if (
                hashlib.sha256(descriptors[0].read_bytes()).hexdigest()
                != DESCRIPTOR_HASH
            ):
                raise RuntimeError("monitor has an unverified HID report descriptor")
            candidates.append(
                (
                    int((device / "busnum").read_text()),
                    int((device / "devnum").read_text()),
                )
            )
        except FileNotFoundError:
            continue
    if len(candidates) != 1:
        raise RuntimeError(
            f"expected one verified MSI controller, found {len(candidates)}"
        )
    return candidates[0]


class USBTransport:
    def __init__(self):
        self.device = None
        self.detached = False
        self.lock = None

    def __enter__(self):
        import usb.backend.libusb1
        import usb.core
        import usb.util

        self.usb = usb
        bus, address = find_monitor()
        library = os.environ.get("MSI_FLASH_LIBUSB")
        backend = (
            usb.backend.libusb1.get_backend(find_library=lambda _: library)
            if library
            else None
        )
        self.device = usb.core.find(
            idVendor=0x1462, idProduct=0x3FA4, bus=bus, address=address, backend=backend
        )
        if self.device is None:
            raise RuntimeError("libusb cannot open the verified monitor")
        lock_dir = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
        self.lock = (lock_dir / "msi-monitor-flash.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.device.is_kernel_driver_active(0):
                self.device.detach_kernel_driver(0)
                self.detached = True
            usb.util.claim_interface(self.device, 0)
            # Drain old OSD polling replies before sending a request.
            for _ in range(32):
                try:
                    self.read(10)
                except TimeoutError:
                    break
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args):
        if self.device is not None:
            try:
                self.usb.util.release_interface(self.device, 0)
                if self.detached:
                    self.device.attach_kernel_driver(0)
            except self.usb.core.USBError:
                # Device may have rebooted. The kernel binds the new instance.
                pass
            finally:
                self.usb.util.dispose_resources(self.device)
        if self.lock:
            self.lock.close()
        self.device = None

    def write(self, data: bytes) -> None:
        if len(data) != 64:
            raise ValueError("not a 64-byte HID report")
        if self.device.write(0x02, data, timeout=2000) != len(data):
            raise RuntimeError("short USB write")

    def read(self, timeout: int = 2000) -> bytes:
        try:
            reply = bytes(self.device.read(0x81, 64, timeout=timeout))
        except self.usb.core.USBTimeoutError as error:
            raise TimeoutError("monitor response timed out") from error
        if len(reply) != 64:
            raise RuntimeError("short USB report")
        return reply

    def exchange(self, data: bytes, delay: float = 0) -> bytes:
        self.write(data)
        time.sleep(delay)
        return self.read()
