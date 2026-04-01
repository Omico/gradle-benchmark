package benchgen.m0501

object Gen0000 {
    @JvmStatic
    fun run(seed: Int): Int {
        var acc = seed
        acc = (acc * 1315423911 + 0) xor (acc ushr 7)
        acc = (acc * 1315423911 + 1) xor (acc ushr 7)
        acc = (acc * 1315423911 + 2) xor (acc ushr 7)
        acc = (acc * 1315423911 + 3) xor (acc ushr 7)
        acc = (acc * 1315423911 + 4) xor (acc ushr 7)
        acc = (acc * 1315423911 + 5) xor (acc ushr 7)
        acc = (acc * 1315423911 + 6) xor (acc ushr 7)
        acc = (acc * 1315423911 + 7) xor (acc ushr 7)
        acc = (acc * 1315423911 + 8) xor (acc ushr 7)
        acc = (acc * 1315423911 + 9) xor (acc ushr 7)
        acc = acc xor Gen0001.run(acc)
        acc = acc xor Gen0002.run(acc)
        acc = acc xor Gen0003.run(acc)
        acc = acc xor Gen0004.run(acc)
        acc = acc xor Gen0005.run(acc)
        return acc
    }
}
