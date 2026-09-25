// ETS2 map extractor: parse base_map.scs via TruckLib and export JSON
// for the neko_pawpilot map knowledge base (roads/services/fuel/company).
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using TruckLib.HashFs;
using TruckLib.ScsMap;

class Program
{
    static int Main(string[] args)
    {
        if (args.Length < 2)
        {
            Console.Error.WriteLine("usage: MapExtractor <base_map.scs> <out_json>");
            Console.Error.WriteLine("  base_map.scs: path to the ETS2 map archive");
            return 1;
        }
        string scsPath = args[0];
        string outJson = args[1];

        try
        {
            Console.WriteLine("Opening HashFS archive: " + scsPath);
            using var fs = HashFsReader.Open(scsPath);
            Console.WriteLine("Archive opened. Opening map...");
            var map = Map.Open("/map/europe", fs);
            Console.WriteLine("Map loaded. Items: " + map.MapItems.Count);

            var facilities = new List<Dictionary<string, object>>();
            var roads = new List<Dictionary<string, object>>();

            foreach (var kv in map.MapItems)
            {
                var item = kv.Value;
                if (item is Road road)
                {
                    if (road.Node == null || road.ForwardNode == null) continue;
                    var a = road.Node.Position;
                    var b = road.ForwardNode.Position;
                    roads.Add(new Dictionary<string, object>
                    {
                        ["id"] = kv.Key.ToString("x"),
                        ["road_type"] = road.RoadType.ToString(),
                        ["x"] = a.X, ["y"] = a.Y, ["z"] = a.Z,
                        ["x2"] = b.X, ["y2"] = b.Y, ["z2"] = b.Z,
                    });
                }
                else if (item is Service svc)
                {
                    string kind = svc.ServiceType switch
                    {
                        ServiceType.GasStation => "fuel",
                        ServiceType.ServiceStation => "service",
                        ServiceType.TruckDealer => "dealer",
                        ServiceType.Parking => "parking",
                        ServiceType.Recruitment => "recruitment",
                        _ => "other",
                    };
                    var pos = svc.Node?.Position;
                    facilities.Add(new Dictionary<string, object>
                    {
                        ["id"] = kv.Key.ToString("x"),
                        ["kind"] = kind,
                        ["name"] = kind,
                        ["x"] = pos?.X ?? 0, ["y"] = pos?.Y ?? 0, ["z"] = pos?.Z ?? 0,
                    });
                }
                else if (item is Company company)
                {
                    var pos = company.Node?.Position;
                    facilities.Add(new Dictionary<string, object>
                    {
                        ["id"] = kv.Key.ToString("x"),
                        ["kind"] = "company",
                        ["name"] = company.CompanyName.ToString(),
                        ["x"] = pos?.X ?? 0, ["y"] = pos?.Y ?? 0, ["z"] = pos?.Z ?? 0,
                    });
                }
                else if (item is Prefab prefab &&
                         prefab.SemaphoreProfile.Value != 0 &&
                         prefab.Nodes.Count > 0)
                {
                    int idx = Math.Min(prefab.Origin, prefab.Nodes.Count - 1);
                    var pos = prefab.Nodes[idx].Position;
                    facilities.Add(new Dictionary<string, object>
                    {
                        ["id"] = kv.Key.ToString("x"),
                        ["kind"] = "traffic_light",
                        ["name"] = prefab.SemaphoreProfile.ToString(),
                        ["x"] = pos.X, ["y"] = pos.Y, ["z"] = pos.Z,
                    });
                }
            }

            var result = new Dictionary<string, object>
            {
                ["version"] = "trucklib-0.5.1",
                ["roads"] = roads,
                ["facilities"] = facilities,
            };
            string json = JsonSerializer.Serialize(result, new JsonSerializerOptions
            {
                WriteIndented = true,
            });
            File.WriteAllText(outJson, json);
            Console.WriteLine("Exported " + roads.Count + " roads, " +
                              facilities.Count + " facilities to " + outJson);
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("ERROR: " + ex.Message);
            Console.Error.WriteLine(ex.StackTrace);
            return 2;
        }
    }
}
