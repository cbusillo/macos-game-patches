using System;
using System.IO;
using System.Linq;
using dnlib.DotNet;
using dnlib.DotNet.Emit;

class Program
{
    static void Main(string[] args)
    {
        if (args.Length == 0)
        {
            Console.WriteLine("Usage: render12_patcher <path-to-VRage.Render12.dll>");
            return;
        }
        var dllPath = Path.GetFullPath(args[0]);
        if (!File.Exists(dllPath)) { Console.WriteLine($"Missing: {dllPath}"); return; }
        var backup = dllPath + ".bak";
        if (!File.Exists(backup)) { File.Copy(dllPath, backup); Console.WriteLine($"Backup written: {backup}"); }
        else Console.WriteLine($"Backup exists: {backup}");

        var mod = ModuleDefMD.Load(dllPath);

        // helper type
        var helper = new TypeDefUser("Keen.VRage.Render12.Core.Systems", "DebugHooks", mod.CorLibTypes.Object.TypeDefOrRef)
        {
            Attributes = TypeAttributes.NotPublic | TypeAttributes.Abstract | TypeAttributes.Sealed | TypeAttributes.BeforeFieldInit
        };
        mod.Types.Add(helper);
        var disableCullFld = new FieldDefUser("DisableCull", new FieldSig(mod.CorLibTypes.Boolean), FieldAttributes.Public | FieldAttributes.Static | FieldAttributes.InitOnly);
        var disableHzbFld = new FieldDefUser("DisableHZB", new FieldSig(mod.CorLibTypes.Boolean), FieldAttributes.Public | FieldAttributes.Static | FieldAttributes.InitOnly);
        helper.Fields.Add(disableCullFld);
        helper.Fields.Add(disableHzbFld);

        var getEnv = mod.Import(typeof(Environment).GetMethod("GetEnvironmentVariable", new[] { typeof(string) }));
        var isNullOrEmpty = mod.Import(typeof(string).GetMethod("IsNullOrEmpty", System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.Public, null, new[] { typeof(string) }, null));
        var stringEquals = mod.Import(typeof(string).GetMethod("Equals", new[] { typeof(string), typeof(string) }));

        var cctor = new MethodDefUser(".cctor", MethodSig.CreateStatic(mod.CorLibTypes.Void),
            MethodImplAttributes.IL | MethodImplAttributes.Managed,
            MethodAttributes.Private | MethodAttributes.Static | MethodAttributes.RTSpecialName | MethodAttributes.SpecialName);
        helper.Methods.Add(cctor);
        var il = cctor.Body = new CilBody { InitLocals = true, KeepOldMaxStack = true };
        var strLocal = new Local(mod.CorLibTypes.String);
        il.Variables.Add(strLocal);

        void EmitEnvBool(string envName, FieldDef target)
        {
            var lblFalse = Instruction.Create(OpCodes.Nop);
            var lblEnd = Instruction.Create(OpCodes.Nop);
            il.Instructions.Add(OpCodes.Ldstr.ToInstruction(envName));
            il.Instructions.Add(OpCodes.Call.ToInstruction(getEnv));
            il.Instructions.Add(OpCodes.Stloc.ToInstruction(strLocal));
            il.Instructions.Add(OpCodes.Ldloc.ToInstruction(strLocal));
            il.Instructions.Add(OpCodes.Call.ToInstruction(isNullOrEmpty));
            il.Instructions.Add(OpCodes.Brtrue_S.ToInstruction(lblFalse));
            il.Instructions.Add(OpCodes.Ldloc.ToInstruction(strLocal));
            il.Instructions.Add(OpCodes.Ldstr.ToInstruction("0"));
            il.Instructions.Add(OpCodes.Call.ToInstruction(stringEquals));
            il.Instructions.Add(OpCodes.Brtrue_S.ToInstruction(lblFalse));
            il.Instructions.Add(OpCodes.Ldc_I4_1.ToInstruction());
            il.Instructions.Add(OpCodes.Stsfld.ToInstruction(target));
            il.Instructions.Add(OpCodes.Br_S.ToInstruction(lblEnd));
            il.Instructions.Add(lblFalse);
            il.Instructions.Add(OpCodes.Ldc_I4_0.ToInstruction());
            il.Instructions.Add(OpCodes.Stsfld.ToInstruction(target));
            il.Instructions.Add(lblEnd);
        }

        EmitEnvBool("SE2_DISABLE_CULL", disableCullFld);
        EmitEnvBool("SE2_DISABLE_HZB", disableHzbFld);
        il.Instructions.Add(OpCodes.Ret.ToInstruction());

        MethodDef MakeGetter(string name, FieldDef fld)
        {
            var m = new MethodDefUser(name, MethodSig.CreateStatic(mod.CorLibTypes.Boolean),
                MethodImplAttributes.IL | MethodImplAttributes.Managed,
                MethodAttributes.Public | MethodAttributes.Static);
            var b = new CilBody { KeepOldMaxStack = true };
            b.Instructions.Add(OpCodes.Ldsfld.ToInstruction(fld));
            b.Instructions.Add(OpCodes.Ret.ToInstruction());
            m.Body = b;
            helper.Methods.Add(m);
            return m;
        }
        var getDisableCull = MakeGetter("IsCullDisabled", disableCullFld);
        var getDisableHZB = MakeGetter("IsHZBDisabled", disableHzbFld);

        // patch BuildHiZBuffer early return
        var sceneDraw = mod.Types.FirstOrDefault(t => t.FullName == "Keen.VRage.Render12.Core.Systems.SceneDrawSystem");
        if (sceneDraw != null)
        {
            var buildHiZ = sceneDraw.Methods.FirstOrDefault(m => m.Name == "BuildHiZBuffer");
            if (buildHiZ != null)
            {
                var b = buildHiZ.Body; b.SimplifyBranches(); b.KeepOldMaxStack = true;
                var first = b.Instructions.First();
                b.Instructions.Insert(0, Instruction.Create(OpCodes.Call, getDisableHZB));
                b.Instructions.Insert(1, Instruction.Create(OpCodes.Brfalse_S, first));
                b.Instructions.Insert(2, Instruction.Create(OpCodes.Ret));
            }
            else Console.WriteLine("BuildHiZBuffer not found");
        }

        // patch CullingJob DoCullingFirstPass/SecondPass to early return
        var cullJobType = mod.Types.FirstOrDefault(t => t.FullName == "Keen.VRage.Render12.GeometryStage.Passes.CullingJob");
        if (cullJobType != null)
        {
            foreach (var methodName in new[] { "DoCullingFirstPass", "DoCullingSecondPass" })
            {
                var m = cullJobType.Methods.FirstOrDefault(x => x.Name == methodName);
                if (m == null) { Console.WriteLine($"{methodName} not found"); continue; }
                var b = m.Body; b.SimplifyBranches(); b.KeepOldMaxStack = true;
                var first = b.Instructions.First();
                b.Instructions.Insert(0, Instruction.Create(OpCodes.Call, getDisableCull));
                b.Instructions.Insert(1, Instruction.Create(OpCodes.Brfalse_S, first));
                b.Instructions.Insert(2, Instruction.Create(OpCodes.Ret));
            }
        }
        else Console.WriteLine("CullingJob type not found");

        mod.Write(dllPath, new dnlib.DotNet.Writer.ModuleWriterOptions(mod) { MetadataOptions = { Flags = dnlib.DotNet.Writer.MetadataFlags.KeepOldMaxStack } });
        Console.WriteLine("Patched VRage.Render12.dll (cull/HZB toggles)");
    }
}
