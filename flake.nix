{
  description = "Development shell with Ansible and local tooling for deploying Arcane";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      devShells = forAllSystems (pkgs:
        let
          pythonEnv = pkgs.python3.withPackages (ps: [
            ps.jinja2
            ps.pyyaml
          ]);
        in
        {
          default = pkgs.mkShell {
            name = "ansible-arcane";
            packages = [
              pkgs.ansible
              pkgs.git
              pkgs.gitleaks
              pkgs.gnumake
              pkgs.openssh
              pkgs.openssl
              pkgs.pre-commit
              pythonEnv
            ];
          };
        }
      );
    };
}
