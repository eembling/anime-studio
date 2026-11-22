"""Round-trip a real Anime Studio cast export through the Manga Studio HTTP API."""
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from contextlib import ExitStack
from pathlib import Path

from studio import Project, handler, Image
import anime_studio as anime
from http.server import ThreadingHTTPServer


class CastExchangeTest(unittest.TestCase):
    def test_round_trip(self):
        with ExitStack() as cleanup:
            root = Path(cleanup.enter_context(tempfile.TemporaryDirectory()))
            source = anime.Studio(root / "anime")
            cleanup.callback(source.db.conn.close)
            target = Project(root / "manga")
            api = anime.API(source)
            actor = api.asset_save(dict(name="Mei", kind="character", description="Red scarf", lora="mei.safetensors",lora_strength=.6))["id"]
            place = api.asset_save(dict(name="Station",kind="location",description="Clock tower"))["id"]
            api.asset_save(dict(name="Letter",kind="prop"))
            api.asset_save(dict(name="Unselected",kind="character"))
            for name,color in [("front","red"),("turn","green"),("station","gray")]:
                Image.new("RGB",(16,20),color).save(source.data / "uploads" / (name+".png"))
            source.db.x("UPDATE assets SET image=? WHERE id=?",("uploads/turn.png",actor))
            source.db.x("UPDATE assets SET image=? WHERE id=?",("uploads/station.png",place))
            source.db.x("INSERT INTO takes(asset_id,stage,path) VALUES(?,'sheet',?)",(actor,"uploads/turn.png"))
            source.creative.look_save(dict(asset_id=actor,name="Costume",references=[dict(path="uploads/front.png",label="Front")]))
            source.db.x("INSERT INTO takes(asset_id,stage,path) VALUES(?,'sheet',?)",(actor,"cast/missing.png"))
            local = target.action("plan",dict(title="Departure",limit=4,script="Mei: Goodbye."))
            panel_asset = local["chapters"][0]["pages"][0]["panels"][0]["cast"][0]

            def serve(factory):
                server = ThreadingHTTPServer(("127.0.0.1",0),factory)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                cleanup.callback(server.server_close)
                cleanup.callback(server.shutdown)
                return f"http://127.0.0.1:{server.server_port}"

            src = serve(anime.make_handler(source,api))
            dst = serve(handler(target))
            with urllib.request.urlopen(src+f"/api/cast/export?ids={actor},{place}") as response:
                self.assertIn("attachment",response.headers["Content-Disposition"])
                bundle = response.read()
            def upload(data):
                request = urllib.request.Request(dst+"/api/cast/import",data=data,headers={"Content-Type":"application/zip"})
                with urllib.request.urlopen(request) as response:
                    return json.load(response)

            result = upload(bundle)
            self.assertEqual(result["counts"],dict(added=1,updated=1,unchanged=0))
            self.assertEqual(len(result["warnings"]),1)
            mei, station = result["state"]["assets"]
            self.assertEqual((mei["id"],mei["description"],mei["lora"],mei["strength"]),(panel_asset,"Red scarf","mei.safetensors",.6))
            self.assertEqual({r["collection"] for r in mei["refs"]},{"Turnarounds","Costume · v1"})
            self.assertEqual(mei["selected"],mei["refs"][0]["id"])
            self.assertEqual(station["kind"],"location")
            for a in (mei,station):
                for r in a["refs"]:
                    self.assertTrue((target.media/r["id"]).is_file())
            with target.edit() as state:
                state["assets"][0].update(description="Ink costume",selected=mei["refs"][1]["id"])
            repeated = upload(bundle)
            self.assertEqual(repeated["counts"],dict(added=0,updated=0,unchanged=2))
            self.assertEqual(repeated["state"]["assets"][0]["description"],"Ink costume")
            self.assertEqual(repeated["state"]["assets"][0]["selected"],mei["refs"][1]["id"])

            # A bad final image rolls back both earlier entries and newly decoded files.
            fresh = Project(root / "fresh")
            invalid = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(bundle)) as original, zipfile.ZipFile(invalid,"w") as out:
                last = original.namelist()[-2]
                for key in original.namelist():
                    out.writestr(key,b"bad" if key==last else original.read(key))
            invalid.seek(0)
            with self.assertRaises(ValueError):
                fresh.import_library(invalid)
            self.assertEqual(fresh.state()["assets"],[])
            self.assertEqual(list(fresh.media.iterdir()),[])
            with self.assertRaises(urllib.error.HTTPError) as error:
                upload(b"invalid archive")
            self.assertEqual(error.exception.code,400)


if __name__ == "__main__":
    unittest.main()
