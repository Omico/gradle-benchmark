package benchgen.m0847;

public final class Gen0003 {
    private Gen0003() {}

    public static int run(int seed) {
        int acc = seed;
        acc = (acc * 1315423911 + 0) ^ (acc >>> 7);
        acc = (acc * 1315423911 + 1) ^ (acc >>> 7);
        acc = (acc * 1315423911 + 2) ^ (acc >>> 7);
        acc = (acc * 1315423911 + 3) ^ (acc >>> 7);
        acc = (acc * 1315423911 + 4) ^ (acc >>> 7);
        acc = (acc * 1315423911 + 5) ^ (acc >>> 7);
        acc = (acc * 1315423911 + 6) ^ (acc >>> 7);
        acc = (acc * 1315423911 + 7) ^ (acc >>> 7);
        acc = (acc * 1315423911 + 8) ^ (acc >>> 7);
        acc = (acc * 1315423911 + 9) ^ (acc >>> 7);
        acc ^= Gen0000.run(acc);
        acc ^= Gen0001.run(acc);
        acc ^= Gen0002.run(acc);
        acc ^= Gen0004.run(acc);
        acc ^= Gen0005.run(acc);
        return acc;
    }
}
