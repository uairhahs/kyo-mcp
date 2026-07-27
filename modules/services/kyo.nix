# ~/Projects/kyo/modules/services/kyo.nix
{ config, lib, pkgs, ... }:

let
  cfg = config.services.kyo;
  
  # Build the python package using the pyproject.toml defined in the project root
  kyoPackage = pkgs.python3.pkgs.buildPythonApplication {
    pname = "kyo-mcp";
    version = "0.1.0";
    src = ../../kyo_mcp;
    
    buildInputs = with pkgs; [ python3Packages.hatchling ];
    
    propagatedBuildInputs = with pkgs.python3.pkgs; [
      mcp-sdk
      fastapi
      networkx
      pydantic
      yaml
    ];
  };

in {
  options.services.kyo = {
    enable = lib.mkEnableOption "Kyō Knowledge Catalogue MCP Server";
    
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
      description = "Kyō Knowledge Catalogue Service User";
    };

    users.groups.kyo = {};

    # Define the systemd service
    systemd.services.kyo-mcp = {
      description = "Kyō Semantic Knowledge Catalogue MCP Server";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" ];

      environmentFile = lib.mkIf (cfg.environmentFile != null) cfg.environmentFile;

      serviceConfig = {
        ExecStart = "${kyoPackage}/bin/kyo-mcp";
        Restart = "on-failure";
        
        # Run as the dedicated user
        User = "kyo";
        Group = "kyo";
        
        # Set the working directory where our DuckDB will live
        WorkingDirectory = "/var/lib/kyo";
        
        # Security Hardening (Best Practice for NixOS)
        ReadWritePaths = [ "/var/lib/kyo" ];
        ProtectSystem = "strict";
        ProtectHome = "yes";
        NoNewPrivileges = true;
        
        # Logging
        StandardOutput = "journal";
        StandardError = "journal";
      };
    };
  };
}
