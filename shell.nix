{
  pkgs ? import <nixpkgs> { },
}:
pkgs.mkShell {
  packages = [
    (pkgs.python3.withPackages (p: [ p.pyusb ]))
    pkgs.ruff
  ];
  MSI_FLASH_LIBUSB = "${pkgs.libusb1}/lib/libusb-1.0.so";
}
