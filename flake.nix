{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      dotnet = pkgs.dotnetCorePackages.dotnet_9.runtime;
      fhs = pkgs.buildFHSEnv {
        name = "tuspyserver";
        targetPkgs =
          ps: with ps; [
            ruff
            uv
            docker_27
          ];
        profile = ''
          export VIRTUAL_ENV=".venv"
        '';
        runScript = "nu -e 'overlay use .venv/bin/activate.nu'";
      };
    in
    {
      devShells.${system}.default = fhs.env;
    };
}
