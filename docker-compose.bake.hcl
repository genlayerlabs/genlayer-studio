variable "FRONTEND_BUILD_TARGET" {
    default = "final"
}

variable "BACKEND_BUILD_TARGET" {
    default = "prod"
}

group "default" {
    targets = ["frontend", "jsonrpc", "webrequest", "hardhat", "database-migration"]
}

target "frontend" {
    context = "."
    dockerfile = "docker/Dockerfile.frontend"
    target = "${FRONTEND_BUILD_TARGET}"
    args = {
        VITE_* = ""
    }
    cache-from = ["type=registry,ref=frontend-cache"]
    cache-to = ["type=registry,ref=frontend-cache,mode=max"]
}

target "jsonrpc" {
    context = "."
    dockerfile = "docker/Dockerfile.backend"
    target = "${BACKEND_BUILD_TARGET}"
    cache-from = ["type=registry,ref=jsonrpc-cache"]
    cache-to = ["type=registry,ref=jsonrpc-cache,mode=max"]
}

target "webrequest" {
    context = "."
    dockerfile = "docker/Dockerfile.webrequest"
    cache-from = ["type=registry,ref=webrequest-cache"]
    cache-to = ["type=registry,ref=webrequest-cache,mode=max"]
}

target "hardhat" {
    context = "."
    dockerfile = "docker/Dockerfile.hardhat"
    cache-from = ["type=registry,ref=hardhat-cache"]
    cache-to = ["type=registry,ref=hardhat-cache,mode=max"]
}

target "database-migration" {
    context = "."
    dockerfile = "docker/Dockerfile.database-migration"
    cache-from = ["type=registry,ref=database-migration-cache"]
    cache-to = ["type=registry,ref=database-migration-cache,mode=max"]
} 