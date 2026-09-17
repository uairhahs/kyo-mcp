# NixOS module for the Kyo Knowledge Catalogue MCP Server.
# Import this file into your NixOS configuration and set
# services.kyo.enable = true; to run it as a systemd service.
{ config, lib, pkgs, ... }:

let
  cfg = config.services.kyo;

  # Default build of kyo-mcp from the pyproject.toml in ../pkg.
  #
  # hindsight-api and mnemosyne-memory aren't packaged in nixpkgs (they're
  # not on PyPI's public index either), so this default build omits them
  # and won't have a fully working Hindsight/Mnemosyne bridge. Override
  # services.kyo.package with a derivation that supplies them (e.g. via an
  # overlay, or a uv2nix/poetry2nix build) instead of editing this module.
  defaultPackage = pkgs.python3.pkgs.buildPythonApplication {
    pname = "kyo-mcp";
    version = "0.1.0";
    src = ../pkg;

    pyprojectToml = ../pkg/pyproject.toml;
    format = "pyproject";
    nativeBuildInputs = with pkgs.python3.pkgs; [ hatchling ];

    propagatedBuildInputs = with pkgs.python3.pkgs; [
      appdirs
      bandit
      fastapi
      httptools
      mcp
      networkx
      pydantic
      pyyaml
      requests
      uvicorn
      uvloop
    ];
  };

in {
  options.services.kyo = {
    enable = lib.mkEnableOption "Kyo Knowledge Catalogue MCP Server";

    package = lib.mkOption {
      type = lib.types.package;
      default = defaultPackage;
      description = ''
        The kyo-mcp package to run. Override this to supply a build that
        includes hindsight-api and mnemosyne-memory, which the default
        build omits since neither is packaged in nixpkgs.
      '';
    };

    environmentFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = "Optional environment file (e.g., for secrets) mounted at /etc/kyo/env";
    };
  };

  config = lib.mkIf cfg.enable {
    # Ensure a dedicated user for the service
    users.users.kyo = {
      isSystemUser = true;
      group = "kyo";
      home = "/var/lib/kyo";
      description = "Kyo Knowledge Catalogue Service User";
    };

    users.groups.kyo = {};

    systemd.services.kyo = {
      description = "Kyo Knowledge Catalogue MCP Server";
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" ];

      preStart = ''
        if [ ! -f /var/lib/kyo/kyo_catalog.db ]; then
          cp ${../pkg/kyo_catalog.db} /var/lib/kyo/kyo_catalog.db
          chmod 644 /var/lib/kyo/kyo_catalog.db
        fi
      '';

      serviceConfig = {
        User = "kyo";
        Group = "kyo";
        WorkingDirectory = "/var/lib/kyo";
        ExecStart = "${cfg.package}/bin/kyo-mcp";
        EnvironmentFile = lib.mkIf (cfg.environmentFile != null) cfg.environmentFile;
        Restart = "on-failure";
      };
    };
  };
}
