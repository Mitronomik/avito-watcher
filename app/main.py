from fastapi import FastAPI
from app.api.routes import router

app = FastAPI(title="Avito Watcher")
app.include_router(router)
