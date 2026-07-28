{ lib, buildNodePackage, super }:
buildNodePackage {
  pname = "kyo";
  version = "0.1.0";
  
  src = ../modules/services/kyo;
  
  nativeBuildInputs = [
    # Dependencies required for better-sqlite3 compilation and runtime
    pkgconfig
  ];
  
  # Add system dependencies via LD_LIBRARY_PATH if necessary during build/run
  propagatedBuildInputs = with super; [
    sqlite openssl zlib
  ];

  meta = {
    description = "An MCP server implementing the Open Knowledge Format (OKF) specification.";
    platforms = ["x86_64-linux"];
    license = lib.licenses.mit;
  };
}
