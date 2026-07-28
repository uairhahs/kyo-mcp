# ~/Projects/kyo/modules/services/kyo.nix
{ config, lib, pkgs, ... }:

let
  cfg = config.services.kyo;
  
  # Build the python package using the pyproject.toml defined in the mcp directory
  kyoPackage = pkgs.python3.pkgs.buildPythonApplication {
    pname = "kyo-mcp";
    version = "0.1.0";
    src = ../../pkg;

    # Set the path to the pyproject.toml file
    pyprojectToml = ../../pkg/pyproject.toml;
    format = "pyproject";
    nativeBuildInputs = with pkgs.python3.pkgs; [ hatchling ];
    
    propagatedBuildInputs = with pkgs.python3.pkgs; [
      fastapi
      graphviz
      httptools
      langchain-community
      mcp
      networkx
      pydantic
      pyyaml
      uvicorn
      uvloop
    ];
  };

in {
  options.services.kyo = {
    enable = lib.mkEnableOption "Kyo Knowledge Catalogue MCP Server";
    
    environmentFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = "Optional environment file (e.g., for secrets) mounted at /etc/kyo/env";
    };
  };

  config.lib.hosts.the-fleet-host = lib.mkIf cfg.enable ''
    # Add this to your the-fleet-host/nixos-config hosts/the-fleet-host/default.nix:
    imports = [ "${config.suffixes.services.kyo}/modules/services/kyo.nix" ];
  '';

  config = lib.mkIf cfg.enable {
    # Ensure a dedicated user for the service
    users.users.kyo = {
      isSystemUser = true;
      group = "kyo";
      home = "/var/lib/kyo";
      description = "Kyo Knowledge Catalogue Service User";
    };

    users.groups.kyo = {};

    # Define the systemd service
    preStart = ''
        if [ ! -f /var/lib/kyo/kyo_catalog.db ]; then
          cp ${../../pkg/kyo_catalog.db} /var/lib/kyo/kyo_catalog.db
          chmod 644 /var/lib/kyo/kyo_catalog.db
        fi
      '';
  };
}
