from theano import Op, Type, Apply, Variable, Constant
from theano import tensor, scalar
import StringIO

import cuda_ndarray
from .type import CudaNdarrayType

class GpuDot22(Op):
    def __str__(self):
        return 'GpuDot22'
    def __eq__(self, other):
        return type(self) == type(other)

    def __hash__(self):
        return hash(type(self))

    def make_node(self, x, y):
        if x.type.ndim != 2:
            raise TypeError(x)
        if y.type.ndim != 2:
            raise TypeError(y)
        return Apply(self, [x,y], [x.type()])

    def c_code_cache_version(self):
        return (1,0)

    def c_code(self, node, nodename, inputs, outputs, sub):
        x, y = inputs
        z, = outputs
        fail = sub['fail']
        return """
        if (cnda_%(x)s->nd != 2)
        {
            PyErr_Format(PyExc_TypeError, "rank(x)==%%i must be 2", cnda_%(x)s->nd);
            %(fail)s;
        }
        if (cnda_%(y)s->nd != 2)
        {
            PyErr_Format(PyExc_TypeError, "rank(y)==%%i must be 2", cnda_%(y)s->nd);
            %(fail)s;
        }
        if ((NULL == cnda_%(z)s)
            || (CudaNdarray_HOST_DIMS(cnda_%(z)s)[0] != CudaNdarray_HOST_DIMS(cnda_%(x)s)[0])
            || (CudaNdarray_HOST_DIMS(cnda_%(z)s)[1] != CudaNdarray_HOST_DIMS(cnda_%(y)s)[1]))
        {
            //if (cnda_%(z)s) Py_DECREF(cnda_%(z)s);
            Py_XDECREF(cnda_%(z)s);
            npy_intp dims[2];
            dims[0] = CudaNdarray_HOST_DIMS(cnda_%(x)s)[0];
            dims[1] = CudaNdarray_HOST_DIMS(cnda_%(y)s)[1];
            cnda_%(z)s = (CudaNdarray*)CudaNdarray_new_null();
            if ((NULL == cnda_%(z)s) || CudaNdarray_alloc_contiguous(cnda_%(z)s, 2, dims))
            {
                if (cnda_%(z)s)
                {
                    Py_DECREF(cnda_%(z)s);
                    cnda_%(z)s = NULL;
                }
                %(fail)s;
            }
        }
        if (CudaNdarray_gemm(1.0f, cnda_%(x)s, cnda_%(y)s, 0.0f, cnda_%(z)s))
        {
            if (cnda_%(z)s)
            {
                Py_DECREF(cnda_%(z)s);
                cnda_%(z)s = NULL;
            }
            %(fail)s;
        }
        """ % locals()
gpu_dot22 = GpuDot22()

class GpuGemm(Op):
    destroy_map = {0:[0]}
    def __str__(self):
        return 'GpuGemm'
    def __eq__(self, other):
        return type(self) == type(other)

    def __hash__(self):
        return hash(type(self))

    def make_node(self, z, a, x, y, b):
        # the more complicated error checking performed by tensor.gemm is assumed to already
        # have been done
        return Apply(self, [z, a, x, y, b], [z.type()])

    def c_code_cache_version(self):
        return (1,0)

    def c_code(self, node, name, inputs, outputs, sub):
        z_in, a, x, y, b = inputs
        z_out, = outputs
        fail = sub['fail']
        return """

        #define REAL float
        float %(name)s_a = (%(a)s->descr->type_num == PyArray_FLOAT) 
        ? (REAL)(((float*)%(a)s->data)[0])
        : (REAL)(((double*)%(a)s->data)[0]);

        float %(name)s_b = (%(b)s->descr->type_num == PyArray_FLOAT) ?
        (REAL)(((float*)%(b)s->data)[0])
        : (REAL)(((double*)%(b)s->data)[0]);
        #undef REAL

        if (CudaNdarray_gemm(%(name)s_a, cnda_%(x)s, cnda_%(y)s, %(name)s_b, cnda_%(z_in)s))
        {
            %(fail)s;
        }
        cnda_%(z_out)s = cnda_%(z_in)s;
        Py_INCREF(cnda_%(z_out)s);
        """ % locals()
gpu_gemm = GpuGemm()

##
# Not really a BLAS operation, but whatever.
#
class GpuConv(Op):
    @staticmethod
    def logical_output_shape_2d(imshp, kshp, mode):
        if mode == 'valid':
            return imshp[0] - kshp[0] + 1, imshp[1] - kshp[1] + 1
        if mode == 'full':
            return imshp[0] + kshp[0] - 1, imshp[1] + kshp[1] - 1
        raise ValueError(mode)

    def __init__(self, border_mode, 
            subsample=(1,1), 
            logical_img_hw=None, 
            logical_kern_hw=None,
            logical_kern_align_top=True):
        self.border_mode = border_mode
        self.subsample = subsample
        if logical_img_hw is not None:
            h,w = logical_img_hw
            #TODO: reconsider this... since shapes are not given in constructor,
            # maybe a multiplier + offset is a more appropriate way of passing this logical
            # grid
        self.logical_img_hw = tuple(logical_img_hw)
        if logical_kern_hw is not None:
            h,w = logical_kern_hw
            #TODO: reconsider this... since shapes are not given in constructor,
            # maybe a multiplier + offset is a more appropriate way of passing this logical
            # grid
        self.logical_kern_hw = tuple(logical_kern_hw)
        self.logical_kern_align_top = logical_kern_align_top

    def __eq__(self, other):
        return type(self) == type(other) \
            and self.border_mode == other.border_mode \
            and self.subsample == other.subsample \
            and self.logical_img_hw == other.logical_img_hw \
            and self.logical_kern_hw == other.logical_kern_hw \
            and self.logical_kern_align_top == other.logical_kern_align_top

    def __hash__(self):
        return hash(type(self)) \
            ^ hash(self.border_mode) \
            ^ hash(self.subsample) \
            ^ hash(self.logical_img_hw) \
            ^ hash(self.logical_kern_hw) \
            ^ hash(self.logical_kern_align_top)

    def __str__(self):
        return '%s{%s, %s, %s, %s, %s}' %(self.__class__.__name__,
                self.border_mode,
                str(self.subsample),
                str(self.logical_img_hw),
                str(self.logical_kern_hw),
                str(self.logical_kern_align_top))

    def make_node(self, img, kern):
        if img.type.ndim != 4:
            raise TypeError('img must be 4D tensor')
        if kern.type.ndim != 4:
            raise TypeError('kern must be 4D tensor')

        broadcastable = [img.type.broadcastable[0], kern.type.broadcastable[0], False, False]
        return Apply(self, [img, kern], [CudaNdarrayType(broadcastable)()])

    def perform(self, node, (img, kern), (out,)):
        print "out", out
        out[0] = cuda_ndarray.conv(img, kern, 
                mode=self.border_mode, 
                subsample=self.subsample,
                logical_img_shape=self.logical_img_hw,
                logical_kern_shape=self.logical_kern_hw,
                kern_align=self.logical_kern_align_top,
                verbose=0)

class GpuDownsampleFactorMax(Op):
    def __init__(self, ds, ignore_border=False):
        self.ds = tuple(ds)
        self.ignore_border = ignore_border

    def __eq__(self, other):
        return type(self) == type(other) and self.ds == other.ds and self.ignore_border == other.ignore_border

    def __hash__(self):
        return hash(type(self)) ^ hash(self.ds) ^ hash(self.ignore_border)

    def __str__(self):
        return '%s{%s,%s}' % (self.__class__.__name__, self.ds, self.ignore_border)

    def make_node(self, x):
        if not isinstance(x.type, CudaNdarrayType):
            raise TypeError()
        if not x.type.ndim == 4:
            raise TypeError()
        return Apply(self, [x], [x.type()])
    #def perform(self, node, input_storage, output_storage):
        #raise NotImplementedError('only C is implemented')
    def c_code_cache_version(self):
        return ()
    def c_code(self, node, nodename, (x,), (z,), sub):
        fail = sub['fail']
        ds0, ds1 = self.ds
        ignore_border = int(self.ignore_border)
        return """
        int dims[4], xdim2, xdim3;
        if (cnda_%(x)s->nd != 4)
        {
            PyErr_SetString(PyExc_ValueError, "rank error");
            %(fail)s;
        }
        xdim2 = CudaNdarray_HOST_DIMS(cnda_%(x)s)[2];
        xdim3 = CudaNdarray_HOST_DIMS(cnda_%(x)s)[3];
        dims[0] = CudaNdarray_HOST_DIMS(cnda_%(x)s)[0];
        dims[1] = CudaNdarray_HOST_DIMS(cnda_%(x)s)[1];
        dims[2] = xdim2 / %(ds0)s;
        dims[3] = xdim3 / %(ds1)s;
        if (! %(ignore_border)s)
        {
            dims[2] += (xdim2%%(%(ds0)s)?1:0);
            dims[3] += (xdim3%%(%(ds1)s)?1:0);
        }
        if(dims[3]>512){
            PyErr_SetString(PyExc_ValueError, "last dimention bigger then 512. This case is not implemented.");
            %(fail)s;
        }

        if ((NULL == cnda_%(z)s)
            || (CudaNdarray_HOST_DIMS(cnda_%(z)s)[0] != dims[0])
            || (CudaNdarray_HOST_DIMS(cnda_%(z)s)[1] != dims[1])
            || (CudaNdarray_HOST_DIMS(cnda_%(z)s)[2] != dims[2])
            || (CudaNdarray_HOST_DIMS(cnda_%(z)s)[3] != dims[3]))
        {
            Py_XDECREF(cnda_%(z)s);
            cnda_%(z)s = (CudaNdarray*)CudaNdarray_new_null();
            if ((NULL == cnda_%(z)s)
                || CudaNdarray_alloc_contiguous(cnda_%(z)s, 4, dims))
            {
                Py_XDECREF(cnda_%(z)s);
                cnda_%(z)s = NULL;
                PyErr_SetString(PyExc_ValueError, "Was not able to allocate output!");
                %(fail)s;
            }
        }
        {
            dim3 grid(dims[0] * dims[1], dims[2]);
            //dim3 block(std::min(dims[3], 512)); //TODO: implement this by supporting more
            //outputs than threads
            dim3 block(dims[3]);
            if ((grid.x*grid.y) && dims[3])
            kMaxPool_%(nodename)s<%(ds0)s, %(ds1)s> <<<grid, block, xdim3*sizeof(float)>>>(
                dims[0], dims[1], dims[2], dims[3], xdim2, xdim3,
                CudaNdarray_DEV_DATA(cnda_%(x)s),
                CudaNdarray_HOST_STRIDES(cnda_%(x)s)[0],
                CudaNdarray_HOST_STRIDES(cnda_%(x)s)[1],
                CudaNdarray_HOST_STRIDES(cnda_%(x)s)[2],
                CudaNdarray_HOST_STRIDES(cnda_%(x)s)[3],
                CudaNdarray_DEV_DATA(cnda_%(z)s));
            CNDA_THREAD_SYNC;
            cudaError_t err = cudaGetLastError();
            if( cudaSuccess != err) 
            {
                PyErr_Format(PyExc_RuntimeError, "Cuda error: %%s: %%s. (grid: %%i x %%i; block: %%i x %%i x %%i)\\n",
                    "kMaxPool_%(nodename)s",
                    cudaGetErrorString(err),
                    grid.x,
                    grid.y,
                    block.x,
                    block.y,
                    block.z);
                %(fail)s;
            }                         
        }
        """ % locals()

    def c_support_code_apply(self, node, nodename):
        ignore_border = int(self.ignore_border)
        return """
        template<int pf2, int pf3>
        __global__ void kMaxPool_%(nodename)s(
           int D0, int D1, int D2, int D3, int xD2, int xD3,
           const float * x, int xS0, int xS1, int xS2, int xS3, 
           float *z)
        {
            float cur_max, cur_x;
            int i0 = blockIdx.x %% D0;
            int i1 = blockIdx.x / D0;
            int i2 = blockIdx.y;

            extern __shared__ float xbuf[]; //size [xD3]

            for (int r2 = 0; (r2 < pf2) && (%(ignore_border)s || (r2 + i2*pf2 < xD2)); ++r2)
            {
                __syncthreads();
                // load the current row of the image into shared memory
                for (int j = threadIdx.x; j < xD3; j += blockDim.x)
                {
                    xbuf[j] = x[i0*xS0 + i1*xS1 + (i2*pf2+r2)*xS2 + j*xS3];
                }
                __syncthreads();
                 
                // initialize our max if this is the first row we're loading
                cur_max = (r2 == 0) ? xbuf[threadIdx.x*pf3] : cur_max;

                // do a mini-reduction over the pf3 relevant elements in the current row
                if (%(ignore_border)s)
                {
                    for (int k = 0; k < pf3; ++k)
                    {
                        cur_x = xbuf[threadIdx.x*pf3+k];
                        cur_max = (cur_x > cur_max) ? cur_x : cur_max;
                    }
                }
                else
                {
                    for (int k = 0; k < pf3; ++k)
                    {
                        if (threadIdx.x*pf3 + k < xD3)
                        {
                            cur_x = xbuf[threadIdx.x*pf3+k];
                            cur_max = (cur_x > cur_max) ? cur_x : cur_max;
                        }
                    }
                }
            }

            //store the result to global memory
            z[i0 * D1*D2*D3 + i1*D2*D3 + i2*D3 + threadIdx.x] = cur_max;
        }
        """ % locals()

class GpuDownsampleFactorMaxGrad(Op):
    def __init__(self, ds, ignore_border):
        self.ds = tuple(ds)
        self.ignore_border = ignore_border

    def __eq__(self, other):
        return type(self) == type(other) and self.ds == other.ds and self.ignore_border == other.ignore_border

    def __hash__(self):
        return hash(type(self)) ^ hash(self.ds) ^ hash(self.ignore_border)

    def __str__(self):
        return '%s{%s,%s}' % (self.__class__.__name__, self.ds, self.ignore_border)

    def make_node(self, x, z, gz):
        return Apply(self, [x, z, gz], [x.type()])
    #def perform(self, node, input_storage, output_storage):
        #raise NotImplementedError('only C is implemented')
    def c_code_cache_version(self):
        return ()
    def c_code(self, node, nodename, (x, z, gz), (gx,), sub):
        fail = sub['fail']
        ds0, ds1 = self.ds
        ignore_border = int(self.ignore_border)
        return """
        if (cnda_%(x)s->nd != 4
            || cnda_%(z)s->nd != 4
            || cnda_%(gz)s->nd != 4)
        {
            PyErr_SetString(PyExc_ValueError, "rank error");
            %(fail)s;
        }
        if ((NULL == cnda_%(gx)s)
            || (CudaNdarray_HOST_DIMS(cnda_%(gx)s)[0] != CudaNdarray_HOST_DIMS(cnda_%(x)s)[0])
            || (CudaNdarray_HOST_DIMS(cnda_%(gx)s)[1] != CudaNdarray_HOST_DIMS(cnda_%(x)s)[1])
            || (CudaNdarray_HOST_DIMS(cnda_%(gx)s)[2] != CudaNdarray_HOST_DIMS(cnda_%(x)s)[2])
            || (CudaNdarray_HOST_DIMS(cnda_%(gx)s)[3] != CudaNdarray_HOST_DIMS(cnda_%(x)s)[3]))
        {
            Py_XDECREF(cnda_%(gx)s);
            cnda_%(gx)s = (CudaNdarray*)CudaNdarray_new_null();
            if ((NULL == cnda_%(gx)s)
                || CudaNdarray_alloc_contiguous(cnda_%(gx)s, 4, CudaNdarray_HOST_DIMS(cnda_%(x)s)))
            {
                Py_XDECREF(cnda_%(gx)s);
                cnda_%(gx)s = NULL;
                %(fail)s;
            }
        }
        {
            //TODO: implement this by supporting more
            //outputs than threads
            dim3 grid(CudaNdarray_HOST_DIMS(cnda_%(x)s)[0], CudaNdarray_HOST_DIMS(cnda_%(x)s)[2]);
            dim3 block(CudaNdarray_HOST_DIMS(cnda_%(x)s)[3]);
            kDownsampleMaxGrad_%(nodename)s<%(ds0)s, %(ds1)s> <<<grid, block>>>(
                CudaNdarray_HOST_DIMS(cnda_%(z)s)[0],
                CudaNdarray_HOST_DIMS(cnda_%(z)s)[1],
                CudaNdarray_HOST_DIMS(cnda_%(z)s)[2],
                CudaNdarray_HOST_DIMS(cnda_%(z)s)[3],
                CudaNdarray_HOST_DIMS(cnda_%(x)s)[2],
                CudaNdarray_HOST_DIMS(cnda_%(x)s)[3],
                CudaNdarray_DEV_DATA(cnda_%(x)s),
                CudaNdarray_HOST_STRIDES(cnda_%(x)s)[0],
                CudaNdarray_HOST_STRIDES(cnda_%(x)s)[1],
                CudaNdarray_HOST_STRIDES(cnda_%(x)s)[2],
                CudaNdarray_HOST_STRIDES(cnda_%(x)s)[3],
                CudaNdarray_DEV_DATA(cnda_%(z)s),
                CudaNdarray_HOST_STRIDES(cnda_%(z)s)[0],
                CudaNdarray_HOST_STRIDES(cnda_%(z)s)[1],
                CudaNdarray_HOST_STRIDES(cnda_%(z)s)[2],
                CudaNdarray_HOST_STRIDES(cnda_%(z)s)[3],
                CudaNdarray_DEV_DATA(cnda_%(gz)s),
                CudaNdarray_HOST_STRIDES(cnda_%(gz)s)[0],
                CudaNdarray_HOST_STRIDES(cnda_%(gz)s)[1],
                CudaNdarray_HOST_STRIDES(cnda_%(gz)s)[2],
                CudaNdarray_HOST_STRIDES(cnda_%(gz)s)[3],
                CudaNdarray_DEV_DATA(cnda_%(gx)s));
            CNDA_THREAD_SYNC;
            cudaError_t err = cudaGetLastError();
            if( cudaSuccess != err) 
            {
                PyErr_Format(PyExc_RuntimeError, "Cuda error: %%s: %%s. (grid: %%i x %%i; block: %%i x %%i x %%i)\\n",
                    "kDownsampleMaxGrad_%(nodename)s",
                    cudaGetErrorString(err),
                    grid.x,
                    grid.y,
                    block.x,
                    block.y,
                    block.z);
                %(fail)s;
            }                         
        }
        """ % locals()

    def c_support_code_apply(self, node, nodename):
        ignore_border = int(self.ignore_border)
        return """
        template<int ds0, int ds1>
        __global__ void kDownsampleMaxGrad_%(nodename)s(
           int D0, int D1, int D2, int D3, int xD2, int xD3,
           const float * x, int xS0, int xS1, int xS2, int xS3, 
           const float * z, int zS0, int zS1, int zS2, int zS3, 
           const float * gz, int gzS0, int gzS1, int gzS2, int gzS3, 
           float *gx)
        {
            float cur_max, cur_x, my_z, my_gz;
            int i0 = blockIdx.x;
            int i1 = 0;
            int i2 = blockIdx.y;       // row wrt z and/or gz
            int x_col = threadIdx.x;

            //TODO: raise occupancy.  Use threadIdx.y to run several iterations of this i1 loop
            //in parallel
            for (i1 = 0; i1 < D1; ++i1)
            {
                // The algorithm here is that every thread writes one output pixel per line
                if (%(ignore_border)s && (x_col >= ds1 * D3))
                {
                    my_gz = 0;
                }
                else
                {
                    my_gz = gz[i0 * gzS0 + i1 * gzS1 + i2 * gzS2 + (x_col/ds1)*gzS3];
                    my_z =   z[i0 *  zS0 + i1 *  zS1 + i2 *  zS2 + (x_col/ds1)* zS3];
                }

                for (int x_row = i2*ds0; (x_row < i2*ds0+ds0) && (%(ignore_border)s || (x_row < xD2)); ++x_row)
                {
                    gx[i0 * D1*xD2*xD3 + i1*xD2*xD3 + x_row*xD3 + x_col]
                       = (my_z == x[i0*xS0 + i1*xS1 + x_row*xS2 + x_col*xS3]) ? my_gz : 0;
                }
            }
        }
        """ % locals()


