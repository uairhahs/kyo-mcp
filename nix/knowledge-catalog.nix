{ buildNodePackage }:

buildNodePackage {
  pname = "knowledge-catalog";
  version = "0.1.0";
  
  src = ../. ;
  
  nativeBuildInputs = [
    # Dependencies required for better-sqlite3 compilation and runtime
    pkgconfig
  ];
  
  # Add system dependencies via LD_LIBRARY_PATH if necessary during build/run
  propagatedBuildInputs = with super; [
    sqlite openssl zlib
  ];

  meta = {
    description = "A persistent MCP knowledge catalogue for the the-fleet-host fleet";
    platforms = ["x86_64-linux"];
    license = lib.licenses.mit;
  };
}
