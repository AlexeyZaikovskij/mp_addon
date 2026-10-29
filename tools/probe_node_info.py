"""Save what MultiProbe answers about its nodes (load, objects, licences, encoders, services) to a JSON file,
to design the server block of the overview. Signs in with a kept account (the jobs one first); prints no
passwords.

    .venv\\Scripts\\python tools\\probe_node_info.py [out.json]
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import mp_client, storage  # noqa: E402

CALLS = [
    ("GET", "/api/Info/GetVersion", None),
    ("GET", "/api/Info/GetBackendLicenseInfo", None),
    ("POST", "/api/Probe/Node/Find", {}),
    ("POST", "/api/Probe/License/Find", {}),
    ("POST", "/api/Probe/StreamEncoder/Find", {}),
    ("POST", "/api/Probe/SlmpGuard/GetServicesStatus", {}),
    ("POST", "/api/Hardware/Device/Find", {}),
    ("POST", "/api/Test/ChartData/Find", {}),
    ("POST", "/api/Recording/Task/Find", {}),
]


def account(server_id: int) -> tuple[str, str] | None:
    cred = storage.get_credential(storage.JOBS, server_id)
    if cred:
        return cred
    from contextlib import closing
    with closing(storage._connect()) as db:
        owners = [r["owner"] for r in db.execute("SELECT DISTINCT owner FROM mp_credential WHERE server_id = ?", (server_id,))]
    for owner in owners:
        cred = storage.get_credential(owner, server_id)
        if cred:
            return cred
    return None


async def call(conn, method, path, body):
    try:
        return await mp_client.call(conn, method, path, body)
    except mp_client.MPError as exc:
        return {"_error": exc.code, "_status": exc.status, "_message": exc.message[:300]}


async def main(out: Path) -> None:
    result = {}
    for srv in storage.list_servers():
        cred = account(srv["id"])
        if cred is None:
            result[srv["name"]] = {"_error": "no kept account for this server"}
            continue
        try:
            token, _ = await mp_client.login(srv["url"], cred[0], cred[1], srv["verify_tls"])
        except mp_client.MPError as exc:
            result[srv["name"]] = {"_error": f"sign-in failed: {exc.code} {exc.message[:200]}"}
            continue
        conn = mp_client.Conn(srv["id"], srv["name"], srv["url"], token, srv["verify_tls"])
        data = {path: await call(conn, m, path, b) for m, path, b in CALLS}
        nodes = data["/api/Probe/Node/Find"]
        for n in nodes if isinstance(nodes, list) else []:
            nid = n.get("nodeID")
            data[f"/api/Probe/Node/GetContent?id={nid}"] = await call(conn, "POST", f"/api/Probe/Node/GetContent?id={nid}", None)
            data[f"/api/Probe/Node/GetSettings/{nid}"] = await call(conn, "GET", f"/api/Probe/Node/GetSettings/{nid}", None)
        result[srv["name"]] = data
        print(f"{srv['name']}: {sum(1 for v in data.values() if not (isinstance(v, dict) and '_error' in v))}/{len(data)} answered")
    await mp_client.close()
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"Saved to {out}")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("node_info.json")))
