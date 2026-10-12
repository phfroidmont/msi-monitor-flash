{
  description = "Native Linux firmware tool for the MSI MPG 321URX monitor";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      pkgsFor = system: import nixpkgs { inherit system; };
    in
    {
      packages = forAllSystems (
        system:
        let
          package = import ./default.nix { pkgs = pkgsFor system; };
        in
        {
          default = package;
          msi-monitor-flash = package;
        }
      );

      apps = forAllSystems (
        system:
        let
          app = {
            type = "app";
            program = "${self.packages.${system}.default}/bin/msi-monitor-flash";
            meta.description = "Experimental native Linux MPG 321URX firmware tool";
          };
        in
        {
          default = app;
          msi-monitor-flash = app;
        }
      );

      devShells = forAllSystems (system: {
        default = import ./shell.nix { pkgs = pkgsFor system; };
      });

      formatter = forAllSystems (system: (pkgsFor system).nixfmt-tree);

      checks = forAllSystems (
        system:
        let
          pkgs = pkgsFor system;
        in
        {
          package = self.packages.${system}.default;
          tests =
            pkgs.runCommand "msi-monitor-flash-tests"
              {
                nativeBuildInputs = [
                  pkgs.python3
                  pkgs.ruff
                ];
              }
              ''
                cp -r ${self} source
                chmod -R u+w source
                cd source
                ruff format --check msi_flash tests
                ruff check msi_flash tests
                python -m unittest discover -s tests -v
                touch "$out"
              '';
        }
      );
    };
}
