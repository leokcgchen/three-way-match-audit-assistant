import uvicorn

from config.settings import settings

if __name__ == "__main__":
    port = int(settings.API_PORT)
    print(f"✅ 截止性测试Agent已启动，端口{port}")
    print("✅ /api/v1/cutoff 已就绪，等待三单系统推送")
    uvicorn.run(
        "src.api.main:app",
        host="0.0.0.0",
        port=port,
        reload=True,
    )
