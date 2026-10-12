{
  pkgs ? import <nixpkgs> { },
}:
let
  source = pkgs.lib.fileset.toSource {
    root = ./.;
    fileset = pkgs.lib.fileset.fileFilter (file: file.hasExt "py") ./msi_flash;
  };
  python = pkgs.python3.withPackages (p: [ p.pyusb ]);
in
pkgs.writeShellApplication {
  name = "msi-monitor-flash";
  runtimeInputs = [ python ];
  text = ''
    export MSI_FLASH_LIBUSB="${pkgs.libusb1}/lib/libusb-1.0.so"
    export PYTHONPATH="${source}"
    exec python3 -m msi_flash "$@"
  '';
}
