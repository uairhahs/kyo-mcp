# NixOS module for the Kyo Knowledge Catalogue MCP Server.
# Import this file into your NixOS configuration and set
# services.kyo.enable = true; to run it as a systemd service.
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
          cp ${../../pkg/kyo_catalog.db} /var/lib/kyo/kyo_catalog.db
          chmod 644 /var/lib/kyo/kyo_catalog.db
        fi
      '';

      serviceConfig = {
        User = "kyo";
        Group = "kyo";
        WorkingDirectory = "/var/lib/kyo";
        ExecStart = "${kyoPackage}/bin/kyo-mcp";
        EnvironmentFile = lib.mkIf (cfg.environmentFile != null) cfg.environmentFile;
        Restart = "on-failure";
      };
    };
  };
}
